const express = require('express');
const router = express.Router();

const { requireAuthentication, attachUserIfPresent } = require('../middleware/auth');
const { loadSchedule, requireReadAccess, requireRole } = require('../middleware/scheduleAccess');
const scheduleController = require('../controllers/scheduleController');
const activityRoutes = require('./activityRoutes');

// IMPORTANT ORDER: these two have no :scheduleId and MUST be
// registered before the router.use('/:scheduleId', ...) below it —
// otherwise Express would match "discover" and "mine" AS IF they
// were a :scheduleId value, and loadSchedule would try (and fail)
// to look up a schedule literally named "discover".
router.get('/discover', scheduleController.discoverPublicSchedules);
router.get('/mine', requireAuthentication, scheduleController.listMySchedules);
router.post('/', requireAuthentication, scheduleController.createSchedule);

router.use('/:scheduleId', loadSchedule);

router.get('/:scheduleId', attachUserIfPresent, requireReadAccess, scheduleController.getSchedule);
router.patch('/:scheduleId', requireAuthentication, requireRole('manager'), scheduleController.updateSchedule);
router.delete('/:scheduleId', requireAuthentication, requireRole('owner'), scheduleController.deleteSchedule);

// Nested activities inherit req.schedule from loadSchedule above —
// no second database lookup needed.
router.use('/:scheduleId/activities', activityRoutes);

module.exports = router;