const express = require('express');
const router = express.Router();

const { requireAuthentication, attachUserIfPresent } = require('../middleware/auth');
const { loadSchedule, requireReadAccess, requireRole } = require('../middleware/scheduleAccess');
const scheduleController = require('../controllers/scheduleController');
const activityRoutes = require('./activityRoutes');

const agentController = require('../controllers/agentController');

const multer = require('multer');
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 10 * 1024 * 1024 } }); // 10MB cap
const knowledgeController = require('../controllers/knowledgeController');

const proposalController = require('../controllers/proposalController');

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
router.post('/:scheduleId/report-problem', requireAuthentication, requireRole('stakeholder'), agentController.reportProblem);
router.post('/:scheduleId/knowledge', requireAuthentication, requireRole('manager'), upload.single('file'), knowledgeController.uploadKnowledge);
router.patch('/:scheduleId', requireAuthentication, requireRole('manager'), scheduleController.updateSchedule);
router.delete('/:scheduleId', requireAuthentication, requireRole('owner'), scheduleController.deleteSchedule);

router.get('/:scheduleId/proposals', requireAuthentication, requireRole('stakeholder'), proposalController.listProposals);
router.patch('/:scheduleId/proposals/:proposalId/decide', requireAuthentication, requireRole('manager'), proposalController.decideProposal);
// Nested activities inherit req.schedule from loadSchedule above —
// no second database lookup needed.
router.use('/:scheduleId/activities', activityRoutes);

module.exports = router;