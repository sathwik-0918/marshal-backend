const express = require('express');
const router = express.Router();
const { requireAuthentication } = require('../middleware/auth');
const notificationController = require('../controllers/notificationController');

router.get('/', requireAuthentication, notificationController.listMyNotifications);
router.patch('/read-all', requireAuthentication, notificationController.markAllRead);

module.exports = router;