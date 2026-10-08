const Schedule = require('../models/Schedule');
const ReferenceEntry = require('../models/ReferenceEntry');
const AuditLog = require('../models/AuditLog');
const { generateAccessCode } = require('../services/accessCode');

async function previewGeneration(req, res, next) {
  try {
    const { requirementText } = req.body;
    if (!requirementText?.trim()) return res.status(400).json({ error: 'requirementText is required' });

    const mlRes = await fetch(`${process.env.ML_SERVICE_URL}/generate/timetable`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ requirement_text: requirementText }),
    });
    const data = await mlRes.json();
    if (!mlRes.ok || !data.success) {
      return res.status(400).json({ error: data.error || 'Could not generate a timetable' });
    }
    res.json(data);
  } catch (err) {
    next(err);
  }
}

async function confirmGeneration(req, res, next) {
  try {
    const { name, description, category, visibility, location, referenceEntries, specTitle, spec, constraints } = req.body;
    if (!name) return res.status(400).json({ error: 'name is required' });
    if (!Array.isArray(referenceEntries) || referenceEntries.length === 0) {
      return res.status(400).json({ error: 'No reference entries to create' });
    }

    const schedule = await Schedule.create({
      name, description: description || specTitle, category, visibility: visibility || 'private', location,
      ownerId: req.user._id,
      members: [{ userId: req.user._id, role: 'owner' }],
      accessCode: visibility !== 'public' ? generateAccessCode() : undefined,
      sourceDocumentType: 'Generated timetable',
      // Kept so the timetable can be revised later by the same solver that built it.
      generationSpec: spec || undefined,
      generationConstraints: Array.isArray(constraints) ? constraints : [],
    });

    for (const entry of referenceEntries) {
      if (!entry.title || entry.entry_type !== 'recurring_weekly') continue;
      await ReferenceEntry.create({
        scheduleId: schedule._id, title: entry.title, entryType: 'recurring_weekly',
        weekday: entry.weekday, startTime: entry.start_time, endTime: entry.end_time,
        venue: entry.venue || '',
        metadata: entry.metadata || [],
      });
    }

    await AuditLog.create({ scheduleId: schedule._id, action: 'schedule_generated', performedBy: req.user._id, after: { scheduleId: schedule._id, count: referenceEntries.length } });
    res.status(201).json({ schedule });
  } catch (err) {
    next(err);
  }
}

async function refineGeneration(req, res, next) {
  try {
    const { spec, rejectedReferenceEntries, feedbackText, objectiveMode, priorConstraints } = req.body;
    if (!spec || !rejectedReferenceEntries || !feedbackText?.trim()) {
      return res.status(400).json({ error: 'spec, rejectedReferenceEntries, and feedbackText are required' });
    }
    const mlRes = await fetch(`${process.env.ML_SERVICE_URL}/generate/refine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        spec, rejected_reference_entries: rejectedReferenceEntries, feedback_text: feedbackText,
        objective_mode: objectiveMode || 'balanced', prior_constraints: priorConstraints || [],
      }),
    });
    const data = await mlRes.json();
    if (!mlRes.ok || !data.success) return res.status(400).json({ error: data.error || 'Could not refine this timetable' });
    res.json(data);
  } catch (err) {
    next(err);
  }
}

async function explainGeneration(req, res, next) {
  try {
    const { spec, referenceEntries, targetEntry, objectiveMode } = req.body;
    if (!spec || !referenceEntries || !targetEntry) {
      return res.status(400).json({ error: 'spec, referenceEntries, and targetEntry are required' });
    }
    const mlRes = await fetch(`${process.env.ML_SERVICE_URL}/generate/explain`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec, reference_entries: referenceEntries, target_entry: targetEntry, objective_mode: objectiveMode || 'balanced' }),
    });
    const data = await mlRes.json();
    if (!mlRes.ok || !data.success) return res.status(400).json({ error: data.error || 'Could not explain this placement' });
    res.json(data);
  } catch (err) {
    next(err);
  }
}

module.exports = { previewGeneration, confirmGeneration, refineGeneration, explainGeneration };