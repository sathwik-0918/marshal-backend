const express = require('express');
const router = express.Router();

router.use('/schedules', require('./scheduleRoutes'));
router.use('/notifications', require('./notificationRoutes'));
router.use('/users', require('./userRoutes'));

module.exports = router;