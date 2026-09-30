const { resolvePendingInvitesForUser } = require('../services/pendingInvites');

async function resolveMyInvites(req, res, next) {
  try {
    const resolvedCount = await resolvePendingInvitesForUser(req.user);
    res.json({ resolvedCount });
  } catch (err) {
    next(err);
  }
}

module.exports = { resolveMyInvites };