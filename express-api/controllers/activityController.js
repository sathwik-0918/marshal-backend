const Activity = require('../models/Activity');
const AuditLog = require('../models/AuditLog');
const { serializeActivity } = require('../utils/serialize');

async function listActivities(req, res, next) {
  try {
    const activities = await Activity.find({ scheduleId: req.schedule._id }).sort({ scheduledStart: 1 });
    res.json(activities.map((a) => serializeActivity(a, req.scheduleRole)));
  } catch (err) {
    next(err);
  }
}

async function createActivity(req, res, next) {
  try {
    const { title, activityType, scheduledStart, durationMinutes, venue, description, priority } = req.body;
    if (!title || !scheduledStart || !durationMinutes || !venue) {
      return res.status(400).json({ error: 'title, scheduledStart, durationMinutes, and venue are required' });
    }

    const activity = await Activity.create({
      scheduleId: req.schedule._id, title, description, activityType, scheduledStart, durationMinutes, venue, priority,
    });

    // Was the gap: creating an activity changes the schedule just as
    // much as editing one does. Now matches updateActivity below.
    const oldVersion = req.schedule.currentVersion;
    req.schedule.currentVersion += 1;
    await req.schedule.save();

    await AuditLog.create({
      scheduleId: req.schedule._id, action: 'activity_created', performedBy: req.user._id,
      oldVersion, newVersion: req.schedule.currentVersion, after: activity.toObject(),
    });

    res.status(201).json(activity);
  } catch (err) {
    next(err);
  }
}

async function updateActivity(req, res, next) {
  try {
    const activity = await Activity.findOne({ _id: req.params.activityId, scheduleId: req.schedule._id });
    if (!activity) return res.status(404).json({ error: 'Activity not found in this schedule' });

    const allowedFields = ['title', 'description', 'activityType', 'scheduledStart', 'durationMinutes', 'venue', 'status', 'priority', 'flexibilityMinutes', 'actualStart', 'actualEnd'];
    const before = activity.toObject();
    allowedFields.forEach((field) => {
      if (req.body[field] !== undefined) activity[field] = req.body[field];
    });
    await activity.save();

    const oldVersion = req.schedule.currentVersion;
    req.schedule.currentVersion += 1;
    await req.schedule.save();

    await AuditLog.create({
      scheduleId: req.schedule._id, action: 'activity_updated', performedBy: req.user._id,
      oldVersion, newVersion: req.schedule.currentVersion, before, after: activity.toObject(),
    });

    res.json(activity);
  } catch (err) {
    next(err);
  }
}

async function deleteActivity(req, res, next) {
  try {
    const activity = await Activity.findOneAndDelete({ _id: req.params.activityId, scheduleId: req.schedule._id });
    if (!activity) return res.status(404).json({ error: 'Activity not found in this schedule' });

    // Same gap as createActivity, same fix.
    const oldVersion = req.schedule.currentVersion;
    req.schedule.currentVersion += 1;
    await req.schedule.save();

    await AuditLog.create({
      scheduleId: req.schedule._id, action: 'activity_deleted', performedBy: req.user._id,
      oldVersion, newVersion: req.schedule.currentVersion, before: activity.toObject(),
    });

    res.status(204).send();
  } catch (err) {
    next(err);
  }
}

module.exports = { listActivities, createActivity, updateActivity, deleteActivity };