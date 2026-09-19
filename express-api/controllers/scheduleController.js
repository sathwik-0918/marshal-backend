const Schedule = require('../models/Schedule');
const Activity = require('../models/Activity');
const AuditLog = require('../models/AuditLog');
const { serializeSchedule } = require('../utils/serialize');

function generateAccessCode() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let code = '';
  for (let i = 0; i < 8; i++) code += chars[Math.floor(Math.random() * chars.length)];
  return code;
}

async function createSchedule(req, res, next) {
  try {
    const { name, description, category, visibility, startDate, endDate, location } = req.body;
    if (!name) return res.status(400).json({ error: 'name is required' });

    const schedule = await Schedule.create({
      name, description, category,
      visibility: visibility || 'private',
      startDate, endDate, location,
      ownerId: req.user._id,
      members: [{ userId: req.user._id, role: 'owner' }],
      accessCode: visibility !== 'public' ? generateAccessCode() : undefined,
    });

    res.status(201).json(schedule); // full shape is correct — the creator is always the owner
  } catch (err) {
    next(err);
  }
}

async function listMySchedules(req, res, next) {
  try {
    const schedules = await Schedule.find({ 'members.userId': req.user._id }).sort({ updatedAt: -1 });
    res.json(schedules); // every result is a schedule the caller already belongs to — no redaction needed
  } catch (err) {
    next(err);
  }
}

async function discoverPublicSchedules(req, res, next) {
  try {
    const { search } = req.query;
    const filter = { visibility: 'public', status: { $ne: 'archived' } };
    if (search) filter.name = { $regex: search, $options: 'i' };
    const schedules = await Schedule.find(filter).sort({ startDate: 1 }).limit(50);
    res.json(schedules.map((s) => serializeSchedule(s, null))); // no auth on this route at all — always public shape
  } catch (err) {
    next(err);
  }
}

async function getSchedule(req, res) {
  res.json({
    schedule: serializeSchedule(req.schedule, req.scheduleRole),
    viewerRole: req.scheduleRole,
    viewerId: req.user?._id ?? null, // the frontend's "My Schedule" filter needs this to match against stakeholders
  });
}

async function updateSchedule(req, res, next) {
  try {
    const allowedFields = ['name', 'description', 'category', 'visibility', 'startDate', 'endDate', 'location', 'status'];
    const before = req.schedule.toObject();

    allowedFields.forEach((field) => {
      if (req.body[field] !== undefined) req.schedule[field] = req.body[field];
    });
    req.schedule.currentVersion += 1;
    await req.schedule.save();

    await AuditLog.create({
      scheduleId: req.schedule._id, action: 'schedule_updated', performedBy: req.user._id,
      oldVersion: before.currentVersion, newVersion: req.schedule.currentVersion, before, after: req.schedule.toObject(),
    });

    res.json(req.schedule);
  } catch (err) {
    next(err);
  }
}

async function deleteSchedule(req, res, next) {
  try {
    // Soft delete. Hard-deleting would orphan every existing AuditLog
    // entry's scheduleId AND leave no record of the deletion itself —
    // the one event most worth a permanent trace of.
    const before = req.schedule.toObject();
    req.schedule.status = 'archived';
    req.schedule.currentVersion += 1;
    await req.schedule.save();

    await AuditLog.create({
      scheduleId: req.schedule._id, action: 'schedule_archived', performedBy: req.user._id,
      oldVersion: before.currentVersion, newVersion: req.schedule.currentVersion, before, after: req.schedule.toObject(),
    });

    res.json(req.schedule); // 200 + archived schedule, not 204 — see note below
  } catch (err) {
    next(err);
  }
}

module.exports = { createSchedule, listMySchedules, discoverPublicSchedules, getSchedule, updateSchedule, deleteSchedule };