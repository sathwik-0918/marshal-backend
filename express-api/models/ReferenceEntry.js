const mongoose = require('mongoose');

const referenceEntrySchema = new mongoose.Schema(
  {
    scheduleId: { type: mongoose.Schema.Types.ObjectId, ref: 'Schedule', required: true },
    title: { type: String, required: true },
    entryType: { type: String, enum: ['recurring_weekly', 'date_range'], required: true },
    description: String,
    weekday: String, // only for recurring_weekly
    startTime: String, // 'HH:MM' local - only for recurring_weekly
    endTime: String,
    startDate: Date, // only for date_range
    endDate: Date,
    venue: String,
    metadata: [String],
  },
  { timestamps: true }
);

referenceEntrySchema.index({ scheduleId: 1 });

module.exports = mongoose.model('ReferenceEntry', referenceEntrySchema);