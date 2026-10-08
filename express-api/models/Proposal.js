const mongoose = require('mongoose');

const optionSchema = new mongoose.Schema(
  {
    description: { type: String, required: true },
    strategy: String,
    risk: { type: String, enum: ['low', 'medium', 'high'] },
    changes: [
      {
        activityId: { type: mongoose.Schema.Types.ObjectId, ref: 'Activity' },
        // Which collection activityId points into - older proposals have none, which means Activity.
        targetModel: { type: String, enum: ['Activity', 'ReferenceEntry'], default: undefined },
        activityTitle: String,
        field: String,
        oldValue: mongoose.Schema.Types.Mixed,
        newValue: mongoose.Schema.Types.Mixed,
      },
    ],
    checks: {
      warnings: [String],
      notes: [String],
      conflictActivityIds: [String],
    },
  },
  { _id: true }
);

const proposalSchema = new mongoose.Schema(
  {
    scheduleId: { type: mongoose.Schema.Types.ObjectId, ref: 'Schedule', required: true },
    triggeringActivityId: { type: mongoose.Schema.Types.ObjectId, ref: 'Activity' },
    requestedBy: { type: mongoose.Schema.Types.ObjectId, ref: 'User' },
    requestText: { type: String, required: true },
    riskTier: { type: String, enum: ['low', 'medium', 'high'], required: true },
    options: [optionSchema],
    // The full set of standing constraints this revision leaves the timetable
    // under - saved to the schedule only if the proposal is approved.
    timetableConstraints: { type: [mongoose.Schema.Types.Mixed], default: undefined },
    status: {
      type: String,
      enum: ['pending', 'approved', 'rejected', 'auto_approved', 'escalated', 'expired'],
      default: 'pending',
    },
    decidedBy: { type: mongoose.Schema.Types.ObjectId, ref: 'User' },
    decidedOptionId: mongoose.Schema.Types.ObjectId,
  },
  { timestamps: true }
);

proposalSchema.index({ scheduleId: 1, status: 1 });

module.exports = mongoose.model('Proposal', proposalSchema);