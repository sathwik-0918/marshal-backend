const mongoose = require('mongoose');

const optionSchema = new mongoose.Schema(
  {
    description: { type: String, required: true },
    strategy: String,
    risk: { type: String, enum: ['low', 'medium', 'high'] },
    changes: [
      {
        activityId: { type: mongoose.Schema.Types.ObjectId, ref: 'Activity' },
        activityTitle: String,
        field: String,
        oldValue: mongoose.Schema.Types.Mixed,
        newValue: mongoose.Schema.Types.Mixed,
      },
    ],
    // Every field the validator produces MUST be declared here. Mongoose strict
    // mode silently deletes anything that isn't, which is how earlier conflict
    // warnings vanished.
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