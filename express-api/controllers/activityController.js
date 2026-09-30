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
    const { title, activityType, scheduledStart, durationMinutes, venue, description, priority, stakeholders, requiredResources, dependencies } = req.body;
    if (!title || !scheduledStart || !durationMinutes || !venue) {
      return res.status(400).json({ error: 'title, scheduledStart, durationMinutes, and venue are required' });
    }

    let validStakeholders = [];
    if (Array.isArray(stakeholders) && stakeholders.length > 0) {
      const memberIds = new Set(req.schedule.members.map((m) => m.userId.toString()));
      validStakeholders = stakeholders
        .map((s) => (typeof s === 'string' ? s : s.userId))
        .filter((id) => memberIds.has(id))
        .map((id) => ({ userId: id, stakeholderRole: 'participant' }));
    }

    let validDependencies = [];
    if (Array.isArray(dependencies) && dependencies.length > 0) {
      const ids = [...new Set(dependencies)];
      const validCount = await Activity.countDocuments({ _id: { $in: ids }, scheduleId: req.schedule._id });
      if (validCount !== ids.length) {
        return res.status(400).json({ error: 'One or more dependencies are not activities on this schedule' });
      }
      validDependencies = ids;
    }

    const activity = await Activity.create({
      scheduleId: req.schedule._id, title, description, activityType, scheduledStart, durationMinutes, venue, priority,
      requiredResources: requiredResources || [],
      stakeholders: validStakeholders,
      dependencies: validDependencies,
    });

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

    if (req.body.dependencies !== undefined) {
      const ids = [...new Set(req.body.dependencies)].filter((id) => id && id !== activity._id.toString());
      const validCount = await Activity.countDocuments({ _id: { $in: ids }, scheduleId: req.schedule._id });
      if (validCount !== ids.length) {
        return res.status(400).json({ error: 'One or more dependencies are not activities on this schedule' });
      }
      activity.dependencies = ids;
    }

    await activity.save();

    req.schedule.currentVersion += 1;
    await req.schedule.save();

    await AuditLog.create({
      scheduleId: req.schedule._id, action: 'activity_updated', performedBy: req.user._id,
      oldVersion: req.schedule.currentVersion - 1, newVersion: req.schedule.currentVersion, before, after: activity.toObject(),
    });

    res.json(activity);
  } catch (err) {
    next(err);
  }
}

async function updateStakeholders(req, res, next) {
  try {
    const { addUserId, removeUserId } = req.body;
    const activity = await Activity.findOne({ _id: req.params.activityId, scheduleId: req.schedule._id });
    if (!activity) return res.status(404).json({ error: 'Activity not found in this schedule' });

    if (addUserId) {
      // Enforced here too, not just in the UI — you can only become an
      // activity stakeholder if you're already a schedule member.
      const isMember = req.schedule.members.some((m) => m.userId.toString() === addUserId);
      if (!isMember) return res.status(400).json({ error: 'This user is not a member of this schedule yet' });
      if (!activity.stakeholders.some((s) => s.userId.toString() === addUserId)) {
        activity.stakeholders.push({ userId: addUserId, stakeholderRole: 'participant' });
      }
    }
    if (removeUserId) {
      activity.stakeholders = activity.stakeholders.filter((s) => s.userId.toString() !== removeUserId);
    }

    await activity.save();
    res.json(activity);
  } catch (err) {
    next(err);
  }
}

async function deleteActivity(req, res, next) {
  try {
    const activity = await Activity.findOneAndDelete({ _id: req.params.activityId, scheduleId: req.schedule._id });
    if (!activity) return res.status(404).json({ error: 'Activity not found in this schedule' });

    // Prevent a dangling reference from lingering forever in some
    // other activity's dependencies array. The Python side already
    // tolerates a dangling id gracefully either way - this is hygiene,
    // not a correctness requirement.
    await Activity.updateMany(
      { scheduleId: req.schedule._id, dependencies: activity._id },
      { $pull: { dependencies: activity._id } }
    );

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

module.exports = { listActivities, createActivity, updateActivity, deleteActivity, updateStakeholders };