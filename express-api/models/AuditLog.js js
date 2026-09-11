const mongoose = require('mongoose');

// This collection doubles as version history — see the note above
// on why there's no separate ScheduleVersion collection.
const auditLogSchema = new mongoose.Schema(
  {
    scheduleId: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'Schedule',
      required: true,
    },
    action: {
      type: String, // 'activity_updated', 'proposal_approved', 'member_added'...
      required: true,
    },
    performedBy: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'User', // absent = the system/agent did this, not a person
    },
    oldVersion: Number,
    newVersion: Number,
    before: mongoose.Schema.Types.Mixed,
    after: mongoose.Schema.Types.Mixed,
    reason: String,
  },
  { timestamps: true }
);

auditLogSchema.index({ scheduleId: 1, createdAt: -1 });

module.exports = mongoose.model('AuditLog', auditLogSchema);