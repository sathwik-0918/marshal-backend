const ReferenceEntry = require('../models/ReferenceEntry');

async function listReferenceEntries(req, res, next) {
  try {
    const entries = await ReferenceEntry.find({ scheduleId: req.schedule._id }).sort({ createdAt: 1 });
    res.json(entries);
  } catch (err) {
    next(err);
  }
}

module.exports = { listReferenceEntries };