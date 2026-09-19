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
      await Notification.create({
        userId: proposal.requestedBy,
        scheduleId: req.schedule._id,
        message: `Your reported issue wasn't applied: "${proposal.requestText}"`,
        type: 'schedule_change',
      });
      return res.json(proposal);
    }

    const chosenOption = proposal.options.id(optionId);
    if (!chosenOption) return res.status(400).json({ error: 'optionId does not match any option on this proposal' });

    const before = req.schedule.toObject();
    // Requester + every stakeholder of every changed activity — collected
    // in the SAME loop that applies the changes, not a second query pass.
    const notifyIds = new Set([proposal.requestedBy.toString()]);

    for (const change of chosenOption.changes) {
      const activity = await Activity.findOne({ _id: change.activityId, scheduleId: req.schedule._id });
      if (!activity) continue;
      activity[change.field] = change.field === 'scheduledStart' ? new Date(change.newValue) : change.newValue;
      await activity.save();
      activity.stakeholders.forEach((s) => notifyIds.add(s.userId.toString()));
    }

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

    await Notification.insertMany(
      [...notifyIds].map((userId) => ({
        userId,
        scheduleId: req.schedule._id,
        activityId: chosenOption.changes[0]?.activityId,
        message: `An update was approved: "${proposal.requestText}"`,
        type: 'schedule_change',
      }))
    );

    res.json(proposal);
  } catch (err) {
    next(err);
  }
}

module.exports = { listProposals, decideProposal };