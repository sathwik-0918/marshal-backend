const User = require('../models/User');
const Activity = require('../models/Activity');
const PendingInvite = require('../models/PendingInvite');
const { addPendingInvite } = require('../services/pendingInvites');
const { ROLE_RANK } = require('../middleware/scheduleAccess');

async function listMembers(req, res, next) {
  try {
    await req.schedule.populate('members.userId', 'name email avatarUrl');
    const pending = await PendingInvite.find({ scheduleId: req.schedule._id, activityIds: { $size: 0 } });
    res.json({ members: req.schedule.members, pending });
  } catch (err) {
    next(err);
  }
}

async function addMember(req, res, next) {
  try {
    const { email, role } = req.body;
    if (!email || !role) return res.status(400).json({ error: 'email and role are required' });

    const elevated = role === 'owner' || role === 'manager';
    const requesterRole = req.schedule.members.find((m) => m.userId.equals(req.user._id))?.role;
    if (elevated && requesterRole !== 'owner') {
      return res.status(403).json({ error: 'Only the owner can grant owner or manager access' });
    }

    const existingUser = await User.findOne({ email: email.toLowerCase().trim() });

    if (existingUser) {
      const alreadyMember = req.schedule.members.some((m) => m.userId.equals(existingUser._id));
      if (alreadyMember) return res.status(409).json({ error: 'Already a member of this schedule' });
      req.schedule.members.push({ userId: existingUser._id, role });
      await req.schedule.save();
      return res.status(201).json({ status: 'added', member: { userId: existingUser._id, role } });
    }

    const invite = await addPendingInvite(req.schedule._id, email, role, null, req.user._id);
    res.status(201).json({ status: 'pending', invite });
  } catch (err) {
    next(err);
  }
}

async function changeMemberRole(req, res, next) {
  try {
    const { role } = req.body;
    const targetUserId = req.params.userId;
    if (!['owner', 'manager', 'stakeholder', 'viewer'].includes(role)) {
      return res.status(400).json({ error: 'Invalid role' });
    }

    const requesterRole = req.schedule.members.find((m) => m.userId.equals(req.user._id))?.role;
    const targetMember = req.schedule.members.find((m) => m.userId.toString() === targetUserId);
    if (!targetMember) return res.status(404).json({ error: 'This user is not a member of this schedule' });

    // Touching owner/manager status in EITHER direction needs owner
    // authority - a manager can freely manage stakeholders/viewers,
    // but can't promote or demote another manager or the owner.
    const touchesElevated = ['owner', 'manager'].includes(role) || ['owner', 'manager'].includes(targetMember.role);
    if (touchesElevated && requesterRole !== 'owner') {
      return res.status(403).json({ error: 'Only the owner can change owner or manager access' });
    }

    if (targetMember.role === 'owner' && role !== 'owner') {
      const ownerCount = req.schedule.members.filter((m) => m.role === 'owner').length;
      if (ownerCount <= 1) {
        return res.status(400).json({ error: 'This schedule needs at least one owner - promote someone else first' });
      }
    }

    targetMember.role = role;
    await req.schedule.save();
    res.json({ userId: targetUserId, role });
  } catch (err) {
    next(err);
  }
}

async function removeMember(req, res, next) {
  try {
    const targetUserId = req.params.userId;
    const requesterRole = req.schedule.members.find((m) => m.userId.equals(req.user._id))?.role;
    const targetMember = req.schedule.members.find((m) => m.userId.toString() === targetUserId);
    if (!targetMember) return res.status(404).json({ error: 'This user is not a member of this schedule' });

    const isSelf = req.user._id.toString() === targetUserId;
    if (!isSelf) {
      // No route-level role gate here on purpose - anyone must be able
      // to remove THEMSELVES (leave a schedule) regardless of their own
      // role, so the nuanced checks live here instead of in middleware.
      const requesterRank = ROLE_RANK[requesterRole] ?? -1;
      if (requesterRank < ROLE_RANK.manager) {
        return res.status(403).json({ error: 'Only a manager or owner can remove another member' });
      }
      if (['owner', 'manager'].includes(targetMember.role) && requesterRole !== 'owner') {
        return res.status(403).json({ error: 'Only the owner can remove an owner or manager' });
      }
    }

    if (targetMember.role === 'owner') {
      const ownerCount = req.schedule.members.filter((m) => m.role === 'owner').length;
      if (ownerCount <= 1) {
        return res.status(400).json({ error: 'This schedule needs at least one owner - promote someone else before removing the last one' });
      }
    }

    req.schedule.members = req.schedule.members.filter((m) => m.userId.toString() !== targetUserId);
    await req.schedule.save();

    // A removed member shouldn't linger as a stakeholder on activities
    // they no longer have any access to.
    await Activity.updateMany(
      { scheduleId: req.schedule._id },
      { $pull: { stakeholders: { userId: targetUserId } } }
    );

    res.json({ removed: targetUserId });
  } catch (err) {
    next(err);
  }
}

module.exports = { listMembers, addMember, changeMemberRole, removeMember };