const mongoose = require('mongoose');

// A member's role belongs to THIS schedule, not to the user globally —
// the same person can be Owner of one event and just a Viewer of another.
const memberSchema = new mongoose.Schema(
  {
    userId: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'User',
      required: true,
    },
    role: {
      type: String,
      enum: ['owner', 'manager', 'stakeholder', 'viewer'],
      default: 'viewer',
    },
    joinedAt: {
      type: Date,
      default: Date.now,
    },
  },
  { _id: false }
);

const scheduleSchema = new mongoose.Schema(
  {
    name: {
      type: String,
      required: true,
    },
    description: String,
    category: {
      type: String,
      enum: ['tournament', 'fest', 'campaign', 'project', 'meeting', 'personal', 'other'],
      default: 'other',
    },
    visibility: {
      type: String,
      enum: ['public', 'private', 'unlisted'],
      default: 'private',
    },
    accessCode: {
      type: String,
      unique: true,
      sparse: true, // only private schedules have this — sparse means the
                     // unique index ignores every document where it's absent,
                     // instead of treating all those "missing" values as duplicates
    },
    startDate: Date,
    endDate: Date,
    location: String,
    status: {
      type: String,
      enum: ['draft', 'live', 'completed', 'archived'],
      default: 'draft',
    },
    ownerId: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'User',
      required: true,
    },
    members: [memberSchema],
    currentVersion: {
      type: Number,
      default: 1,
    },
  },
  { timestamps: true }
);

// Powers the "My Schedules" dashboard — find every schedule where this user is a member.
scheduleSchema.index({ 'members.userId': 1 });

module.exports = mongoose.model('Schedule', scheduleSchema);