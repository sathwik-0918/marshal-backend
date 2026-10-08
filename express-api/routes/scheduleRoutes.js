const express = require('express');
const router = express.Router();

const { requireAuthentication, attachUserIfPresent } = require('../middleware/auth');
const { loadSchedule, requireReadAccess, requireRole } = require('../middleware/scheduleAccess');
const scheduleController = require('../controllers/scheduleController');
const referenceEntryController = require('../controllers/referenceEntryController');
const scheduleFromFileController = require('../controllers/scheduleFromFileController');
const generateController = require('../controllers/generateController');
const activityRoutes = require('./activityRoutes');

const agentController = require('../controllers/agentController');

const multer = require('multer');
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 10 * 1024 * 1024 } }); // 10MB cap
const knowledgeController = require('../controllers/knowledgeController');

const proposalController = require('../controllers/proposalController');

const memberController = require('../controllers/memberController');
const bulkImportController = require('../controllers/bulkImportController');

// IMPORTANT ORDER: these two have no :scheduleId and MUST be
// registered before the router.use('/:scheduleId', ...) below it —
// otherwise Express would match "discover" and "mine" AS IF they
// were a :scheduleId value, and loadSchedule would try (and fail)
// to look up a schedule literally named "discover".
router.get('/discover', scheduleController.discoverPublicSchedules);
router.get('/mine', requireAuthentication, scheduleController.listMySchedules);
router.post('/', requireAuthentication, scheduleController.createSchedule);
router.post('/from-file/preview', requireAuthentication, upload.single('file'), scheduleFromFileController.previewFromFile);
router.post('/from-file/preview-document', requireAuthentication, upload.single('file'), scheduleFromFileController.previewFromDocument);
router.post('/from-file/confirm', requireAuthentication, scheduleFromFileController.confirmFromFile);
// Generation routes - MUST stay above router.use('/:scheduleId', loadSchedule),
// same as /discover, /mine and /from-file/*. Below that line, "generate" is
// read as a schedule ID and Mongoose throws the CastError you're seeing.
router.post('/generate/preview', requireAuthentication, generateController.previewGeneration);
router.post('/generate/confirm', requireAuthentication, generateController.confirmGeneration);
router.post('/generate/refine', requireAuthentication, generateController.refineGeneration);
router.post('/generate/explain', requireAuthentication, generateController.explainGeneration);

router.use('/:scheduleId', loadSchedule);

router.get('/:scheduleId', attachUserIfPresent, requireReadAccess, scheduleController.getSchedule);
router.get('/:scheduleId/reference-entries', attachUserIfPresent, requireReadAccess, referenceEntryController.listReferenceEntries);
router.post('/:scheduleId/report-problem', requireAuthentication, requireRole('stakeholder'), agentController.reportProblem);
router.post('/:scheduleId/knowledge', requireAuthentication, requireRole('manager'), upload.single('file'), knowledgeController.uploadKnowledge);
router.patch('/:scheduleId', requireAuthentication, requireRole('manager'), scheduleController.updateSchedule);
router.delete('/:scheduleId', requireAuthentication, requireRole('owner'), scheduleController.deleteSchedule);

router.get('/:scheduleId/proposals', requireAuthentication, requireRole('stakeholder'), proposalController.listProposals);
router.patch('/:scheduleId/proposals/:proposalId/decide', requireAuthentication, requireRole('manager'), proposalController.decideProposal);
// Nested activities inherit req.schedule from loadSchedule above —
// no second database lookup needed.
router.use('/:scheduleId/activities', activityRoutes);

router.get('/:scheduleId/members', requireAuthentication, requireRole('viewer'), memberController.listMembers);
router.post('/:scheduleId/members', requireAuthentication, requireRole('manager'), memberController.addMember);
router.post('/:scheduleId/activities/bulk-import/preview', requireAuthentication, requireRole('manager'), upload.single('file'), bulkImportController.previewImport);
router.post('/:scheduleId/activities/bulk-import/confirm', requireAuthentication, requireRole('manager'), bulkImportController.confirmImport);

router.patch('/:scheduleId/members/:userId', requireAuthentication, requireRole('manager'), memberController.changeMemberRole);
router.delete('/:scheduleId/members/:userId', requireAuthentication, memberController.removeMember);

module.exports = router;