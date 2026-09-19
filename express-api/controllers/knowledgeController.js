const AuditLog = require('../models/AuditLog');

async function uploadKnowledge(req, res, next) {
  try {
    if (!req.file) return res.status(400).json({ error: 'No file provided' });

    const formData = new FormData();
    formData.append('schedule_id', req.schedule._id.toString());
    formData.append('file', new Blob([req.file.buffer]), req.file.originalname);
    // Node's built-in fetch/FormData/Blob (18+) — no extra package needed,
    // exactly why Phase 1 set that engine minimum.

    const mlRes = await fetch(`${process.env.ML_SERVICE_URL}/knowledge/ingest`, {
      method: 'POST',
      body: formData,
    });

    if (!mlRes.ok) throw new Error(`Knowledge service error: ${await mlRes.text()}`);
    const result = await mlRes.json();

    await AuditLog.create({
      scheduleId: req.schedule._id,
      action: 'knowledge_document_uploaded',
      performedBy: req.user._id,
      after: { filename: result.filename, chunksAdded: result.chunksAdded },
    });

    res.status(201).json(result);
  } catch (err) {
    next(err);
  }
}

module.exports = { uploadKnowledge };