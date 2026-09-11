const mongoose = require('mongoose');

const stakeholderSchema = new mongoose.Schema(
  {
    userId: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'User',
      required: true,
    },
    stakeholderRole: {
      type: String, // 'lead', 'participant', 'volunteer', 'judge', etc.
      default: 'participant',
    },
  },
  { _id: false }
);

const activitySchema = new mongoose.Schema(
  {
    scheduleId: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'Schedule',
      required: true,
    },
    title: {
      type: String,
      required: true,
    },
    description: String,
    activityType: {
      type: String, // 'match', 'workshop', 'session', 'ceremony', 'meeting'...
      default: 'session',
    },

    // scheduledStart + durationMinutes is the ONE source of truth for timing.
    // Deliberately no separate scheduledEnd field — storing both risks them
    // silently drifting apart if someone updates one and forgets the other.
    // Compute end time in code: scheduledStart + durationMinutes.
    scheduledStart: {
      type: Date,
      required: true,
    },
    durationMinutes: {
      type: Number,
      required: true,
    },

    // Filled in once the activity actually happens — this is exactly the
    // data your ML models will eventually train on for real, once you have it.
    actualStart: Date,
    actualEnd: Date,

    venue: {
      type: String,
      required: true,
    },
    status: {
      type: String,
      enum: ['scheduled', 'in_progress', 'delayed', 'completed', 'cancelled'],
      default: 'scheduled',
    },
    priority: {
      type: String,
      enum: ['low', 'medium', 'high'],
      default: 'medium',
    },
    flexibilityMinutes: {
      type: Number,
      default: 0,
    },
    riskTier: {
      type: String,
      enum: ['low', 'medium', 'high'],
      default: 'medium',
    },

    stakeholders: [stakeholderSchema],

    dependencies: [
      {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Activity',
      },
    ],

    constraints: {
      minRestMinutesBefore: { type: Number, default: 0 },
      requiresVenueExclusive: { type: Boolean, default: true },
    },

    // Cached ML output so the frontend isn't calling ml-agent-service on every render.
    mlPredictions: {
      predictedDurationMinutes: Number,
      delayProbability: Number,
      predictedAt: Date,
    },
  },
  { timestamps: true }
);

// The two queries you'll run constantly, both indexed.
activitySchema.index({ scheduleId: 1, scheduledStart: 1 });

module.exports = mongoose.model('Activity', activitySchema);