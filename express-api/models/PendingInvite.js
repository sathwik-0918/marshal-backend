const mongoose = require('mongoose');

const pendingInviteSchema = new mongoose.Schema(
  {
    scheduleId: { type: mongoose.Schema.Types.ObjectId, ref: 'Schedule', required: true },
    email: { type: String, required: true, lowercase: true, trim: true },
    role: { type: String, enum: ['manager', 'stakeholder', 'viewer'], default: 'stakeholder' },
    // Which activities to attach them to once resolved. Empty = just a
    // schedule-level invite (added via the People panel, not bulk import).
    activityIds: [{ type: mongoose.Schema.Types.ObjectId, ref: 'Activity' }],
    invitedBy: { type: mongoose.Schema.Types.ObjectId, ref: 'User' },
  },
  { timestamps: true }
);

// One pending record per email per schedule — a second invite (another
// activity, another bulk import) merges into it instead of duplicating.
pendingInviteSchema.index({ scheduleId: 1, email: 1 }, { unique: true });

module.exports = mongoose.model('PendingInvite', pendingInviteSchema);