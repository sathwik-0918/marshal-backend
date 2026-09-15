// Centralized error handling — routes call next(err) instead of each
// duplicating response logic. Must be registered LAST in server.js,
// after every route: Express identifies error middleware by its
// 4-argument signature and only routes registered before it reach it.
function errorHandler(err, req, res, next) {
  console.error(err);

  if (err.name === 'ValidationError') {
    return res.status(400).json({ error: 'Validation failed', details: err.message });
  }
  if (err.name === 'CastError') {
    return res.status(400).json({ error: 'Invalid ID format' });
  }
  if (err.code === 11000) {
    return res.status(409).json({ error: 'A record with that value already exists' });
  }

  res.status(err.status || 500).json({ error: err.message || 'Something went wrong' });
}

module.exports = errorHandler;