const Schedule = require('../models/Schedule');
const Activity = require('../models/Activity');
const AuditLog = require('../models/AuditLog');
const { parseCSV, validateRow, markDuplicates, annotateEmailStatus, inferDateRange } = require('../services/csvImport');
const { addPendingInvite } = require('../services/pendingInvites');
const { generateAccessCode } = require('../services/accessCode');

async function previewFromFile(req, res, next) {
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
    await annotateEmailStatus(rows, new Set()); // nothing is "already a member" of a schedule that doesn't exist yet

    const suggested = inferDateRange(rows);
    res.json({ rows, suggested });
  } catch (err) {
    next(err);
  }
}

async function confirmFromFile(req, res, next) {
  try {
    const { name, description, category, visibility, startDate, endDate, location, rows } = req.body;
    if (!name) return res.status(400).json({ error: 'name is required' });
    if (!Array.isArray(rows) || rows.length === 0) return res.status(400).json({ error: 'At least one activity row is required' });

    const schedule = await Schedule.create({
      name, description, category,
      visibility: visibility || 'private',
      startDate, endDate, location,
      ownerId: req.user._id,
      members: [{ userId: req.user._id, role: 'owner' }],
      accessCode: visibility !== 'public' ? generateAccessCode() : undefined,
    });

    const summary = { activitiesCreated: 0, stakeholdersLinked: 0, pendingInvitesCreated: 0, rowErrors: [] };

    for (const raw of rows) {
      const row = validateRow(raw, raw.rowIndex);
      if (!row.valid) {
        summary.rowErrors.push(`Row ${row.rowIndex}: ${row.errors.join(', ')}`);
        continue;
      }

      const requiredResources = (row.requiredResources || '').split(';').map((s) => s.trim()).filter(Boolean);
      const activity = await Activity.create({
        scheduleId: schedule._id,
        title: row.title,
        activityType: row.activityType || 'session',
        scheduledStart: new Date(row.scheduledStart),
        durationMinutes: Number(row.durationMinutes),
        venue: row.venue,
        description: row.description || undefined,
        requiredResources,
      });
      summary.activitiesCreated += 1;

      // The schedule was JUST created, so nobody but you is a member
      // yet - every participant email becomes a pending invite here,
      // even one belonging to an existing MARSHAL user elsewhere. It
      // resolves the moment they join or sign in, same as always.
      const emails = (row.participantEmails || '').split(';').map((e) => e.trim().toLowerCase()).filter(Boolean);
      for (const email of emails) {
        await addPendingInvite(schedule._id, email, 'stakeholder', activity._id, req.user._id);
        summary.pendingInvitesCreated += 1;
      }
      if (emails.length > 0) await activity.save();
    }

    if (summary.activitiesCreated === 0) {
      // Don't leave an empty, confusing schedule behind if literally
      // nothing in the file was usable.
      await schedule.deleteOne();
      return res.status(400).json({ error: 'None of the rows were valid, so no schedule was created.', rowErrors: summary.rowErrors });
    }

    await AuditLog.create({ scheduleId: schedule._id, action: 'schedule_created_from_file', performedBy: req.user._id, after: { scheduleId: schedule._id, ...summary } });

    res.status(201).json({ schedule, ...summary });
  } catch (err) {
    next(err);
  }
}

module.exports = { previewFromFile, confirmFromFile };