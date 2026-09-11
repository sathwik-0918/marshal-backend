const mongoose = require('mongoose');

const optionSchema = new mongoose.Schema(
  {
    description: {
      type: String,
      required: true,
    },
    changes: [
      {
        activityId: { type: mongoose.Schema.Types.ObjectId, ref: 'Activity' },
        field: String,
        oldValue: mongoose.Schema.Types.Mixed,
        newValue: mongoose.Schema.Types.Mixed,
      },
    ],
    mlContext: {
      predictedDurationMinutes: Number,
      delayProbability: Number,
    },
  },
  { _id: true } // this one DOES need its own id — approval references "which option was picked"
);

const proposalSchema = new mongoose.Schema(
  {
    scheduleId: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'Schedule',
      required: true,
    },
    triggeringActivityId: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'Activity',
    },
    requestedBy: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'User',
    },
    requestText: {
      type: String,
      required: true,
    },
    riskTier: {
      type: String,
      enum: ['low', 'medium', 'high'],
      required: true,
    },
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