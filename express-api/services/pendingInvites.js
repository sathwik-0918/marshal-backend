const PendingInvite = require('../models/PendingInvite');
const Schedule = require('../models/Schedule');
const Activity = require('../models/Activity');

async function addPendingInvite(scheduleId, email, role, activityId, invitedBy) {
  const update = { $setOnInsert: { scheduleId, email: email.toLowerCase().trim(), role, invitedBy } };
  if (activityId) update.$addToSet = { activityIds: activityId };
  return PendingInvite.findOneAndUpdate(
    { scheduleId, email: email.toLowerCase().trim() },
    update,
    { upsert: true, new: true }
  );
}

// Called whenever we know a real User now exists for a given email —
// on fresh signup (Case C) and on dashboard load for existing users
// newly invited to something (Case B). Idempotent: safe to call
// repeatedly, resolved invites are deleted so there's nothing left to
// re-resolve next time.
async function resolvePendingInvitesForUser(user) {
  const invites = await PendingInvite.find({ email: user.email.toLowerCase() });

  for (const invite of invites) {
    const schedule = await Schedule.findById(invite.scheduleId);
    if (!schedule) {
      await invite.deleteOne(); // schedule was deleted since the invite was made — nothing to resolve, clean up
      continue;
    }

    const alreadyMember = schedule.members.some((m) => m.userId.equals(user._id));
    if (!alreadyMember) {
      schedule.members.push({ userId: user._id, role: invite.role });
      await schedule.save();
    }

    if (invite.activityIds.length > 0) {
      await Activity.updateMany(
        { _id: { $in: invite.activityIds } },
        { $addToSet: { stakeholders: { userId: user._id, stakeholderRole: 'participant' } } }
      );
    }

    await invite.deleteOne();
  }

  return invites.length;
}

module.exports = { addPendingInvite, resolvePendingInvitesForUser };