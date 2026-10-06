const { parse } = require('csv-parse/sync');
const User = require('../models/User');

function parseCSV(buffer) {
  return parse(buffer, { columns: true, skip_empty_lines: true, trim: true });
}

function stripUndefined(value) {
  const trimmed = String(value || '').trim();
  return trimmed === 'UNDEFINED' ? '' : trimmed;
}

function detectCsvShape(headers) {
  const h = new Set(headers.map((x) => x.trim()));
  if (h.has('scheduledStart') && h.has('durationMinutes')) return 'activity';
  if (h.has('weekday')) return 'recurring';
  if (h.has('startDate') && h.has('endDate')) return 'daterange';
  return null;
}

function parseRecurringRow(row) {
  return {
    entry_type: 'recurring_weekly',
    title: row.title || '',
    weekday: (row.weekday || '').trim(),
    start_time: stripUndefined(row.startTime),
    end_time: stripUndefined(row.endTime),
    venue: stripUndefined(row.venue),
    metadata: (row.metadata || '').split(';').map((s) => s.trim()).filter((s) => s && s !== 'UNDEFINED'),
  };
}

function parseDateRangeRow(row) {
  return {
    entry_type: 'date_range',
    title: row.title || '',
    start_date: (row.startDate || '').trim(),
    end_date: (row.endDate || '').trim(),
    description: stripUndefined(row.description),
    metadata: (row.metadata || '').split(';').map((s) => s.trim()).filter((s) => s && s !== 'UNDEFINED'),
  };
}

function validateReferenceEntry(entry, rowIndex) {
  const errors = [];
  const warnings = [];
  if (!entry.title?.trim()) errors.push('Missing title');
  if (entry.entry_type === 'recurring_weekly') {
    const validDays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    if (!validDays.includes(entry.weekday)) errors.push('Missing or invalid weekday');
    if (!entry.start_time) warnings.push('No start time - will display as menu-style, no fixed clock time');
  } else if (entry.entry_type === 'date_range') {
    if (!entry.start_date || Number.isNaN(new Date(entry.start_date).getTime())) errors.push('Missing or invalid start date');
    if (!entry.end_date || Number.isNaN(new Date(entry.end_date).getTime())) errors.push('Missing or invalid end date');
  } else {
    errors.push('Unrecognized entry type');
  }
  return { ...entry, rowIndex, valid: errors.length === 0, errors, warnings };
}

function validateRow(row, rowIndex) {
  const errors = [];
  const warnings = [];

  const required = ['title', 'scheduledStart', 'durationMinutes'];
  const missing = required.filter((f) => !String(row[f] || '').trim());
  if (missing.length > 0) errors.push(`Missing ${missing.join(', ')}`);

  if (row.scheduledStart?.trim() === 'UNDEFINED') {
    errors.push('No time given (marked UNDEFINED) - this needs one to be created as a dated activity');
  } else if (row.scheduledStart && !missing.includes('scheduledStart')) {
    if (Number.isNaN(new Date(row.scheduledStart).getTime())) errors.push('Invalid date/time format');
  }

  if (row.durationMinutes !== undefined && row.durationMinutes !== '' && !missing.includes('durationMinutes')) {
    const duration = Number(row.durationMinutes);
    if (!Number.isFinite(duration) || duration <= 0) {
      errors.push('Duration must be a positive number');
    } else if (duration > 1440) {
      warnings.push('Over 24 hours - double check this is correct');
    }
  }

  if (!String(row.venue || '').trim()) {
    warnings.push('No venue given - fine for things like exams or calendar periods with no assigned room, but confirm that\'s actually correct');
  }

  return {
    rowIndex,
    title: row.title || '',
    activityType: stripUndefined(row.activityType) || 'session',
    scheduledStart: row.scheduledStart || '',
    durationMinutes: row.durationMinutes || '',
    venue: stripUndefined(row.venue),
    description: stripUndefined(row.description),
    participantEmails: stripUndefined(row.participantEmails),
    requiredResources: stripUndefined(row.requiredResources),
    valid: errors.length === 0,
    errors,
    warnings,
  };
}

function markDuplicates(rows) {
  const seen = new Map();
  for (const row of rows) {
    if (!row.valid) continue;
    const key = `${row.title.trim().toLowerCase()}|${row.venue.trim().toLowerCase()}|${row.scheduledStart}`;
    if (seen.has(key)) {
      row.warnings.push(`Looks identical to row ${seen.get(key)} - check this isn't a duplicate`);
    } else {
      seen.set(key, row.rowIndex);
    }
  }
}

async function annotateEmailStatus(rows, memberIds) {
  // memberIds: Set of user _id strings already on the schedule. Pass an
  // empty Set for a schedule that doesn't exist yet - every email will
  // then correctly show as "pending", since nobody can be a member of
  // something that isn't created yet.
  const allEmails = new Set();
  rows.forEach((row) => {
    (row.participantEmails || '').split(';').map((e) => e.trim().toLowerCase()).filter(Boolean).forEach((e) => allEmails.add(e));
  });
  if (allEmails.size === 0) return;

  const users = await User.find({ email: { $in: [...allEmails] } });
  const userByEmail = new Map(users.map((u) => [u.email.toLowerCase(), u]));

  rows.forEach((row) => {
    const emails = (row.participantEmails || '').split(';').map((e) => e.trim().toLowerCase()).filter(Boolean);
    if (emails.length === 0) return;
    let members = 0, pending = 0;
    emails.forEach((email) => {
      const user = userByEmail.get(email);
      if (user && memberIds.has(user._id.toString())) members += 1;
      else pending += 1;
    });
    const parts = [];
    if (members) parts.push(`${members} member${members > 1 ? 's' : ''}`);
    if (pending) parts.push(`${pending} pending invite${pending > 1 ? 's' : ''}`);
    row.emailSummary = parts.join(', ');
  });
}

function inferDateRange(rows) {
  const validDates = rows
    .filter((r) => r.valid)
    .map((r) => new Date(r.scheduledStart))
    .filter((d) => !Number.isNaN(d.getTime()));
  if (validDates.length === 0) return { startDate: null, endDate: null };
  return {
    startDate: new Date(Math.min(...validDates)).toISOString().slice(0, 10),
    endDate: new Date(Math.max(...validDates)).toISOString().slice(0, 10),
  };
}

module.exports = { parseCSV, validateRow, markDuplicates, annotateEmailStatus, inferDateRange, detectCsvShape, parseRecurringRow, parseDateRangeRow, validateReferenceEntry };