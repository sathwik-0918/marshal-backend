const Proposal = require('../models/Proposal');
const Activity = require('../models/Activity');
const AuditLog = require('../models/AuditLog');
const Notification = require('../models/Notification');

async function listProposals(req, res, next) {
  try {
    const status = req.query.status || 'pending';
    const proposals = await Proposal.find({ scheduleId: req.schedule._id, status }).sort({ createdAt: -1 });
    res.json(proposals);
  } catch (err) {
    next(err);
  }
}

async function decideProposal(req, res, next) {
  try {
    const { decision, optionId } = req.body;
    if (!['approve', 'reject'].includes(decision)) {
      return res.status(400).json({ error: "decision must be 'approve' or 'reject'" });
    }

    const proposal = await Proposal.findOne({ _id: req.params.proposalId, scheduleId: req.schedule._id });
    if (!proposal) return res.status(404).json({ error: 'Proposal not found' });
    if (proposal.status !== 'pending') {
      return res.status(409).json({ error: `Proposal already ${proposal.status}` });
    }

    if (decision === 'reject') {
      proposal.status = 'rejected';
      proposal.decidedBy = req.user._id;
      await proposal.save();
      await AuditLog.create({ scheduleId: req.schedule._id, action: 'proposal_rejected', performedBy: req.user._id, after: proposal.toObject() });
      if (proposal.requestedBy) {
        await Notification.create({
          userId: proposal.requestedBy,
          scheduleId: req.schedule._id,
          message: `Your reported issue wasn't applied: "${proposal.requestText}"`,
          type: 'schedule_change',
        });
      }
      return res.json(proposal);
    }

    const chosenOption = proposal.options.id(optionId);
    if (!chosenOption) return res.status(400).json({ error: 'optionId does not match any option on this proposal' });

    const activityIds = [...new Set(chosenOption.changes.map((c) => c.activityId.toString()))];
    const activities = await Activity.find({ _id: { $in: activityIds }, scheduleId: req.schedule._id });
    const byId = new Map(activities.map((a) => [a._id.toString(), a]));

    const stale = chosenOption.changes.some((c) => {
      const act = byId.get(c.activityId.toString());
      if (!act) return true;
      const current = c.field === 'scheduledStart' ? act.scheduledStart.getTime() : String(act[c.field]);
      const expected = c.field === 'scheduledStart' ? new Date(c.oldValue).getTime() : String(c.oldValue);
      return current !== expected;
    });
    if (stale) {
      proposal.status = 'expired';
      await proposal.save();
      return res.status(409).json({
        error: 'This proposal is out of date - the schedule changed after it was created. Ask MARSHAL again for a fresh one.',
      });
    }

    // Apply every change to the in-memory documents first, capturing
    // each field's true original value (only on its FIRST touch, so
    // an activity with two changed fields doesn't lose one on rollback),
    // then validate every touched document before writing anything at all.
    const originalValues = new Map();
    for (const change of chosenOption.changes) {
      const act = byId.get(change.activityId.toString());
      if (!originalValues.has(act._id.toString())) originalValues.set(act._id.toString(), {});
      const orig = originalValues.get(act._id.toString());
      if (!(change.field in orig)) orig[change.field] = act[change.field];
      act[change.field] = change.field === 'scheduledStart' ? new Date(change.newValue) : change.newValue;
    }

    for (const act of byId.values()) {
      try {
        await act.validate();
      } catch (validationErr) {
        return res.status(400).json({ error: `This change isn't valid for ${act.title}: ${validationErr.message}` });
      }
    }

    // Not a database transaction - see note above on why. This is a
    // manual compensating rollback: if any save fails partway through,
    // everything already written gets put back, best-effort.
    const saved = [];
    try {
      for (const act of byId.values()) {
        await act.save();
        saved.push(act._id.toString());
      }
    } catch (saveErr) {
      for (const id of saved) {
        const act = byId.get(id);
        const orig = originalValues.get(id) || {};
        Object.entries(orig).forEach(([field, value]) => { act[field] = value; });
        await act.save().catch(() => {});
      }
      return res.status(500).json({ error: 'Could not apply this change completely - nothing was modified. Try again.' });
    }

    const notifyIds = new Set(proposal.requestedBy ? [proposal.requestedBy.toString()] : []);
    for (const act of byId.values()) {
      act.stakeholders.forEach((s) => notifyIds.add(s.userId.toString()));
    }

    const before = req.schedule.toObject();
    req.schedule.currentVersion += 1;
    await req.schedule.save();

    proposal.status = 'approved';
    proposal.decidedBy = req.user._id;
    proposal.decidedOptionId = chosenOption._id;
    await proposal.save();

    await AuditLog.create({
      scheduleId: req.schedule._id,
      action: 'proposal_approved',
      performedBy: req.user._id,
      oldVersion: before.currentVersion,
      newVersion: req.schedule.currentVersion,
      after: { proposalId: proposal._id, appliedChanges: chosenOption.changes },
    });

    if (notifyIds.size > 0) {
      await Notification.insertMany(
        [...notifyIds].map((userId) => ({
          userId,
          scheduleId: req.schedule._id,
          activityId: chosenOption.changes[0]?.activityId,
          message: `An update was approved: "${proposal.requestText}"`,
          type: 'schedule_change',
        }))
      );
    }

    res.json(proposal);
  } catch (err) {
    next(err);
  }
}

module.exports = { listProposals, decideProposal };