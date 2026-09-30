const { parse } = require('csv-parse/sync');
const User = require('../models/User');

function parseCSV(buffer) {
  return parse(buffer, { columns: true, skip_empty_lines: true, trim: true });
}

function validateRow(row, rowIndex) {
  const errors = [];
  const warnings = [];

  const required = ['title', 'scheduledStart', 'durationMinutes', 'venue'];
  const missing = required.filter((f) => !String(row[f] || '').trim());
  if (missing.length > 0) errors.push(`Missing ${missing.join(', ')}`);

  if (row.scheduledStart && !missing.includes('scheduledStart')) {
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

  return {
    rowIndex,
    title: row.title || '',
    activityType: row.activityType || 'session',
    scheduledStart: row.scheduledStart || '',
    durationMinutes: row.durationMinutes || '',
    venue: row.venue || '',
    description: row.description || '',
    participantEmails: row.participantEmails || '',
    requiredResources: row.requiredResources || '',
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

module.exports = { parseCSV, validateRow, markDuplicates, annotateEmailStatus, inferDateRange };