const express = require('express');
const router = express.Router();

router.use('/schedules', require('./scheduleRoutes'));

module.exports = router;