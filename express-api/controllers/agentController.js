const Activity = require('../models/Activity');
const Proposal = require('../models/Proposal');
const AuditLog = require('../models/AuditLog');
const Notification = require('../models/Notification');

async function reportProblem(req, res, next) {
  try {
    const { message } = req.body;
    if (!message) return res.status(400).json({ error: 'message is required' });

    const activities = await Activity.find({ scheduleId: req.schedule._id }).sort({ scheduledStart: 1 });

    const agentRes = await fetch(`${process.env.ML_SERVICE_URL}/agent/process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        schedule_id: req.schedule._id.toString(),
        schedule_name: req.schedule.name,
        message,
        activities: activities.map((a) => ({
          _id: a._id.toString(),
          title: a.title,
          venue: a.venue,
          scheduledStart: a.scheduledStart.toISOString(),
          durationMinutes: a.durationMinutes,
          activityType: a.activityType,
          status: a.status,
          stakeholders: a.stakeholders,
          requiredResources: a.requiredResources,
          dependencies: (a.dependencies || []).map((d) => d.toString()),
        })),
      }),
    });

    if (!agentRes.ok) throw new Error(`Agent service error: ${await agentRes.text()}`);
    const { understood, proposal } = await agentRes.json();

    if (proposal.needs_clarification) {
      return res.json({ needsClarification: true, question: proposal.clarification_question });
    }

    const activityMap = new Map(activities.map((a) => [a._id.toString(), a]));
    const affectedIds = understood.affected_activity_ids || [];

    const created = await Proposal.create({
      scheduleId: req.schedule._id,
      triggeringActivityId: affectedIds[0] || undefined,
      requestedBy: req.user._id,
      requestText: message,
      riskTier: proposal.risk_tier,
      options: proposal.options.map((opt) => ({
        description: opt.description,
        strategy: opt.strategy,
        risk: opt.risk,
        changes: opt.changes.map((c) => {
          const act = activityMap.get(c.activity_id);
          const oldValue = act ? (c.field === 'scheduledStart' ? act.scheduledStart.toISOString() : act[c.field]) : undefined;
          return {
            activityId: c.activity_id,
            activityTitle: act?.title || 'Unknown activity',
            field: c.field,
            oldValue,
            newValue: c.new_value,
          };
        }),
        checks: {
          warnings: opt.checks?.warnings || [],
          notes: opt.checks?.notes || [],
          conflictActivityIds: opt.checks?.conflict_activity_ids || [],
        },
      })),
      status: 'pending',
    });

    await AuditLog.create({ scheduleId: req.schedule._id, action: 'proposal_created', performedBy: req.user._id, after: created.toObject() });

    const notifyRecipients = req.schedule.members.filter(
      (m) => (m.role === 'owner' || m.role === 'manager') && m.userId.toString() !== req.user._id.toString()
    );
    if (notifyRecipients.length > 0) {
      await Notification.insertMany(
        notifyRecipients.map((m) => ({
          userId: m.userId,
          scheduleId: req.schedule._id,
          activityId: affectedIds[0] || undefined,
          message: `New proposal needs review: "${message.slice(0, 80)}"`,
          type: 'proposal_needs_approval',
        }))
      );
    }

    res.status(201).json({ needsClarification: false, proposal: created });
  } catch (err) {
    next(err);
  }
}

module.exports = { reportProblem };