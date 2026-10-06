const Schedule = require('../models/Schedule');
const Activity = require('../models/Activity');
const ReferenceEntry = require('../models/ReferenceEntry');
const AuditLog = require('../models/AuditLog');
const { parseCSV, validateRow, markDuplicates, annotateEmailStatus, inferDateRange, detectCsvShape, parseRecurringRow, parseDateRangeRow, validateReferenceEntry } = require('../services/csvImport');
const { addPendingInvite } = require('../services/pendingInvites');
const { generateAccessCode } = require('../services/accessCode');

function parseDateSafe(value) {
  if (!value) return undefined;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? undefined : d;
}

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
    const { name, description, category, visibility, startDate, endDate, location, rows, referenceEntries, additionalNotes, detectedScheduleType, orgContext } = req.body;
    if (!name) return res.status(400).json({ error: 'name is required' });
    const hasRows = Array.isArray(rows) && rows.length > 0;
    const hasRefs = Array.isArray(referenceEntries) && referenceEntries.length > 0;
    if (!hasRows && !hasRefs) {
      return res.status(400).json({ error: 'At least one activity or reference entry is required' });
    }

    const schedule = await Schedule.create({
      name, description, category,
      visibility: visibility || 'private',
      startDate, endDate, location,
      sourceDocumentType: detectedScheduleType || undefined,
      sourceContext: orgContext || undefined,
      ownerId: req.user._id,
      members: [{ userId: req.user._id, role: 'owner' }],
      accessCode: visibility !== 'public' ? generateAccessCode() : undefined,
    });

    const summary = { activitiesCreated: 0, stakeholdersLinked: 0, pendingInvitesCreated: 0, referenceEntriesCreated: 0, rowErrors: [] };

    for (const raw of (rows || [])) {
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

    // Skip/coerce malformed entries defensively rather than crash the whole import.
    for (const entry of (referenceEntries || [])) {
      if (!entry.title || !['recurring_weekly', 'date_range'].includes(entry.entry_type)) continue;
      await ReferenceEntry.create({
        scheduleId: schedule._id,
        title: entry.title,
        entryType: entry.entry_type,
        description: entry.description || '',
        weekday: entry.weekday || undefined,
        startTime: entry.start_time || undefined,
        endTime: entry.end_time || undefined,
        startDate: parseDateSafe(entry.start_date),
        endDate: parseDateSafe(entry.end_date),
        venue: entry.venue || '',
        metadata: entry.metadata || [],
      });
      summary.referenceEntriesCreated += 1;
    }

    if (summary.activitiesCreated === 0 && summary.referenceEntriesCreated === 0) {
      await schedule.deleteOne();
      return res.status(400).json({ error: 'None of the rows were valid, so no schedule was created.', rowErrors: summary.rowErrors });
    }

    if (additionalNotes && additionalNotes.trim()) {
      try {
        const notesForm = new FormData();
        notesForm.append('schedule_id', schedule._id.toString());
        notesForm.append('file', new Blob([additionalNotes]), 'extracted-notes.txt');
        await fetch(`${process.env.ML_SERVICE_URL}/knowledge/ingest`, { method: 'POST', body: notesForm });
      } catch (knowledgeErr) {
        console.error('Failed to save extracted notes as knowledge:', knowledgeErr);
        // Non-fatal - the schedule and its activities already exist successfully;
        // losing the advisory notes shouldn't roll back a real import.
      }
    }

    await AuditLog.create({ scheduleId: schedule._id, action: 'schedule_created_from_file', performedBy: req.user._id, after: { scheduleId: schedule._id, ...summary } });

    res.status(201).json({ schedule, ...summary });
  } catch (err) {
    next(err);
  }
}

function extractedRowToCsvRow(extracted, rowIndex) {
  const hasDate = Boolean(extracted.date);
  const hasTime = Boolean(extracted.time);
  const scheduledStart = hasDate && hasTime ? `${extracted.date}T${extracted.time}:00` : '';

  const emails = [];
  const nameOnlyResources = [];
  (extracted.participant_references || []).forEach((ref) => {
    const trimmed = (ref || '').trim();
    if (!trimmed) return;
    if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) emails.push(trimmed);
    else nameOnlyResources.push(trimmed);
  });

  const row = {
    title: extracted.title || '',
    activityType: extracted.activity_type || 'session',
    scheduledStart,
    durationMinutes: extracted.duration_minutes ? String(extracted.duration_minutes) : '',
    venue: extracted.venue || '',
    description: extracted.description || '',
    participantEmails: emails.join(';'),
    requiredResources: [...(extracted.required_resources || []), ...nameOnlyResources].join(';'),
  };

  const validated = validateRow(row, rowIndex);
  if (!hasDate || !hasTime) {
    validated.warnings.push('No exact date/time was found in the document - fill this in before importing');
  }
  if (extracted.confidence === 'low') {
    validated.warnings.push("AI extraction wasn't fully confident about this row - double check the details");
  }
  if (nameOnlyResources.length > 0) {
    validated.warnings.push(`${nameOnlyResources.join(', ')} named with no email - added as a required resource, not a notified participant`);
  }
  return validated;
}

async function previewFromDocument(req, res, next) {
  try {
    if (!req.file) return res.status(400).json({ error: 'No file provided' });

    const filename = (req.file.originalname || '').toLowerCase();

    let rawRows = [];
    let rawReferenceEntries = [];
    let detectedScheduleType = '';
    let additionalNotes = '';
    let orgContext = {};

    if (filename.endsWith('.csv')) {
      const parsed = parseCSV(req.file.buffer);
      const shape = parsed.length > 0 ? detectCsvShape(Object.keys(parsed[0])) : null;
      if (shape === 'activity') {
        rawRows = parsed.map((row, i) => validateRow(row, i + 1));
        detectedScheduleType = 'MARSHAL activity template';
      } else if (shape === 'recurring') {
        rawReferenceEntries = parsed.map(parseRecurringRow);
        detectedScheduleType = 'MARSHAL recurring-weekly template';
      } else if (shape === 'daterange') {
        rawReferenceEntries = parsed.map(parseDateRangeRow);
        detectedScheduleType = 'MARSHAL date-range template';
      }
    }

    if (!rawRows.length && !rawReferenceEntries.length) {
      const formData = new FormData();
      formData.append('file', new Blob([req.file.buffer]), req.file.originalname);
      const mlRes = await fetch(`${process.env.ML_SERVICE_URL}/schedule/extract`, { method: 'POST', body: formData });
      const data = await mlRes.json();
      if (!mlRes.ok || !data.success) {
        return res.status(400).json({ error: data.error || 'Could not extract a schedule from this file' });
      }
      const extraction = data.extraction;
      rawRows = (extraction.activities || []).map((a, i) => extractedRowToCsvRow(a, i + 1));
      rawReferenceEntries = extraction.reference_entries || [];
      detectedScheduleType = extraction.detected_schedule_type || '';
      additionalNotes = extraction.additional_notes || '';
      orgContext = {
        orgName: extraction.org_name || '',
        department: extraction.department || '',
        academicTerm: extraction.academic_term || '',
        location: extraction.location || '',
      };
    }

    markDuplicates(rawRows);
    await annotateEmailStatus(rawRows, new Set());
    const referenceEntries = rawReferenceEntries.map((e, i) => validateReferenceEntry(e, i + 1));
    const suggested = inferDateRange(rawRows);

    res.json({ rows: rawRows, referenceEntries, suggested, detectedScheduleType, additionalNotes, orgContext });
  } catch (err) {
    next(err);
  }
}

module.exports = { previewFromFile, confirmFromFile, previewFromDocument };