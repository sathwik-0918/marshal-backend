const { parseCSV, validateRow, markDuplicates, annotateEmailStatus } = require('../services/csvImport');
const Activity = require('../models/Activity');
const AuditLog = require('../models/AuditLog');
const User = require('../models/User');
const { addPendingInvite } = require('../services/pendingInvites');

async function previewImport(req, res, next) {
  try {
    if (!req.file) return res.status(400).json({ error: 'No CSV file provided' });
    let parsed;
    try {
      parsed = parseCSV(req.file.buffer);
    } catch (parseErr) {
      return res.status(400).json({ error: `Could not parse CSV: ${parseErr.message}` });
    }

    const rows = parsed.map((row, i) => validateRow(row, i + 2));
    markDuplicates(rows);
    const memberIds = new Set(req.schedule.members.map((m) => m.userId.toString()));
    await annotateEmailStatus(rows, memberIds);
    res.json({ rows });
  } catch (err) {
    next(err);
  }
}

async function confirmImport(req, res, next) {
  try {
    const { rows } = req.body;
    if (!Array.isArray(rows)) return res.status(400).json({ error: 'rows array is required' });

    const summary = { activitiesCreated: 0, stakeholdersLinked: 0, pendingInvitesCreated: 0, rowErrors: [] };

    for (const raw of rows) {
      const row = validateRow(raw, raw.rowIndex);
      if (!row.valid) {
        summary.rowErrors.push(`Row ${row.rowIndex}: ${row.errors.join(', ')}`);
        continue;
      }

      const requiredResources = (row.requiredResources || '').split(';').map((s) => s.trim()).filter(Boolean);
      const activity = await Activity.create({
        scheduleId: req.schedule._id,
        title: row.title,
        activityType: row.activityType || 'session',
        scheduledStart: new Date(row.scheduledStart),
        durationMinutes: Number(row.durationMinutes),
        venue: row.venue,
        description: row.description || undefined,
        requiredResources,
      });
      summary.activitiesCreated += 1;

      const emails = (row.participantEmails || '').split(';').map((e) => e.trim().toLowerCase()).filter(Boolean);
      for (const email of emails) {
        const existingUser = await User.findOne({ email });
        const isMember = existingUser && req.schedule.members.some((m) => m.userId.equals(existingUser._id));
        if (existingUser && isMember) {
          activity.stakeholders.push({ userId: existingUser._id, stakeholderRole: 'participant' });
          summary.stakeholdersLinked += 1;
        } else {
          await addPendingInvite(req.schedule._id, email, 'stakeholder', activity._id, req.user._id);
          summary.pendingInvitesCreated += 1;
        }
      }
      if (emails.length > 0) await activity.save();
    }

    if (summary.activitiesCreated > 0) {
      req.schedule.currentVersion += 1;
      await req.schedule.save();
      await AuditLog.create({ scheduleId: req.schedule._id, action: 'bulk_import', performedBy: req.user._id, after: summary });
    }

    res.status(201).json(summary);
  } catch (err) {
    next(err);
  }
}

module.exports = { previewImport, confirmImport };