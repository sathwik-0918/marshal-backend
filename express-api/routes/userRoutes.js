const express = require('express');
const router = express.Router();
const { requireAuthentication } = require('../middleware/auth');
const userController = require('../controllers/userController');

router.post('/resolve-invites', requireAuthentication, userController.resolveMyInvites);

module.exports = router;