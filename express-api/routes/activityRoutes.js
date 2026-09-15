const express = require('express');
const router = express.Router({ mergeParams: true }); // required to see :scheduleId from the parent router

const { requireAuthentication, attachUserIfPresent } = require('../middleware/auth');
const { requireReadAccess, requireRole } = require('../middleware/scheduleAccess');
const activityController = require('../controllers/activityController');

// req.schedule is already loaded by scheduleRoutes.js before this router is reached.

router.get('/', attachUserIfPresent, requireReadAccess, activityController.listActivities);
router.post('/', requireAuthentication, requireRole('manager'), activityController.createActivity);
router.patch('/:activityId', requireAuthentication, requireRole('manager'), activityController.updateActivity);
router.delete('/:activityId', requireAuthentication, requireRole('manager'), activityController.deleteActivity);

module.exports = router;