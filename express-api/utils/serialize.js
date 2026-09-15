// Shared response-shaping so a non-member viewer (public or
// code-based access) never receives internal fields — membership,
// the access secret, per-activity stakeholder identities. Members
// (viewerRole truthy) see the full document, including accessCode,
// since owners/managers legitimately need it to share with invitees.
function serializeSchedule(schedule, viewerRole) {
  const obj = schedule.toObject ? schedule.toObject() : schedule;
  if (viewerRole) return obj;
  const { members, accessCode, ...publicFields } = obj;
  return publicFields;
}

function serializeActivity(activity, viewerRole) {
  const obj = activity.toObject ? activity.toObject() : activity;
  if (viewerRole) return obj;
  const { stakeholders, ...publicFields } = obj;
  return publicFields;
}

module.exports = { serializeSchedule, serializeActivity };