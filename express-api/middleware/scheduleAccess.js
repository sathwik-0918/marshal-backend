const Schedule = require('../models/Schedule');

const ROLE_RANK = { viewer: 0, stakeholder: 1, manager: 2, owner: 3 };

function getMembership(schedule, userId) {
  if (!userId) return null; // guard first — never call .equals(undefined)
  const member = schedule.members.find((m) => m.userId.equals(userId));
  return member ? member.role : null;
}

// Loads the schedule by :scheduleId onto req.schedule. Registered as
// router.use('/:scheduleId', loadSchedule) BEFORE the exact-path
// routes below it — this order matters, see scheduleRoutes.js.
async function loadSchedule(req, res, next) {
  try {
    const schedule = await Schedule.findById(req.params.scheduleId);
    if (!schedule) return res.status(404).json({ error: 'Schedule not found' });
    req.schedule = schedule;
    next();
  } catch (err) {
    next(err);
  }
}

// Read access mirrors the exact model already built on the frontend —
// enforced here for real, since the frontend check is only UX, not
// security. req.user is optional; this must work for anonymous callers.
function requireReadAccess(req, res, next) {
  const schedule = req.schedule;
  const role = getMembership(schedule, req.user?._id);

  if (role) {
    req.scheduleRole = role;
    return next();
  }
  if (schedule.visibility === 'public') {
    req.scheduleRole = null;
    return next();
  }

  const providedCode = req.headers['x-access-code'] || req.query.accessCode;
  if (schedule.accessCode && providedCode === schedule.accessCode) {
    req.scheduleRole = null;
    return next();
  }

  return res.status(403).json({ error: 'This schedule is private. Provide a valid access code or sign in as a member.' });
}

// Write access: requires requireAuthentication to have already run.
// Being able to VIEW a public schedule never implies being able to
// CHANGE it — that always needs a stored role.
function requireRole(minRole) {
  return (req, res, next) => {
    const role = getMembership(req.schedule, req.user?._id);
    if (!role || ROLE_RANK[role] < ROLE_RANK[minRole]) {
      return res.status(403).json({ error: `Requires ${minRole} role or higher on this schedule` });
    }
    req.scheduleRole = role;
    next();
  };
}

module.exports = { loadSchedule, requireReadAccess, requireRole };