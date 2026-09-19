const Activity = require("../models/Activity");
const Proposal = require("../models/Proposal");
const AuditLog = require("../models/AuditLog");
const Notification = require("../models/Notification");

async function reportProblem(req, res, next) {
  try {
    const { message } = req.body;
    if (!message) return res.status(400).json({ error: "message is required" });

    const activities = await Activity.find({
      scheduleId: req.schedule._id,
    }).sort({ scheduledStart: 1 });

    const agentRes = await fetch(
      `${process.env.ML_SERVICE_URL}/agent/process`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
            stakeholders: a.stakeholders,
          })),
        }),
      },
    );

    if (!agentRes.ok)
      throw new Error(`Agent service error: ${await agentRes.text()}`);
    const { understood, proposal } = await agentRes.json();

    if (proposal.needs_clarification) {
      return res.json({
        needsClarification: true,
        question: proposal.clarification_question,
      });
    }

    const relevantActivity = activities.find(
      (a) => a._id.toString() === understood.activity_id,
    );

    const created = await Proposal.create({
      scheduleId: req.schedule._id,
      triggeringActivityId: understood.activity_id || undefined,
      requestedBy: req.user._id,
      requestText: message,
      riskTier: proposal.risk_tier,
      options: proposal.options.map((opt) => {
        const changes = [];
        if (opt.new_venue) {
          changes.push({
            activityId: understood.activity_id,
            field: "venue",
            oldValue: relevantActivity?.venue,
            newValue: opt.new_venue,
          });
        }
        if (opt.new_start_time) {
          changes.push({
            activityId: understood.activity_id,
            field: "scheduledStart",
            oldValue: relevantActivity?.scheduledStart?.toISOString(),
            newValue: opt.new_start_time,
          });
        }
        return {
          description: opt.description,
          changes,
          mlContext: { conflictDetected: opt.conflict_detected || false },
        };
      }),
      status: "pending",
    });

    await AuditLog.create({
      scheduleId: req.schedule._id,
      action: "proposal_created",
      performedBy: req.user._id,
      after: created.toObject(),
    });
    // After the existing Proposal.create(...) and AuditLog.create(...) calls:
    const notifyRecipients = req.schedule.members.filter(
      (m) =>
        (m.role === "owner" || m.role === "manager") &&
        m.userId.toString() !== req.user._id.toString(),
    );
    if (notifyRecipients.length > 0) {
      await Notification.insertMany(
        notifyRecipients.map((m) => ({
          userId: m.userId,
          scheduleId: req.schedule._id,
          activityId: understood.activity_id || undefined,
          message: `New proposal needs review: "${message.slice(0, 80)}"`,
          type: "proposal_needs_approval",
        })),
      );
    }

    res.status(201).json({ needsClarification: false, proposal: created });
  } catch (err) {
    next(err);
  }
}

module.exports = { reportProblem };
