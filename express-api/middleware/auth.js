const { clerkMiddleware, getAuth, clerkClient } = require('@clerk/express');
const User = require('../models/User');

// Verifies the Clerk session if one exists and attaches req.auth.
// Applied globally in server.js. Never blocks by itself — public
// routes must keep working for anonymous visitors.
const clerkAuth = clerkMiddleware();

// Shared by both auth paths below, so "create a local User from a
// Clerk profile" exists in exactly one place, not two copies that
// can drift apart.
async function findOrCreateUser(clerkUserId) {
  let user = await User.findOne({ clerkId: clerkUserId });
  if (user) return user;

  const clerkUser = await clerkClient.users.getUser(clerkUserId);
  return User.create({
    clerkId: clerkUserId,
    name: `${clerkUser.firstName ?? ''} ${clerkUser.lastName ?? ''}`.trim() || 'Unnamed',
    email: clerkUser.emailAddresses[0]?.emailAddress ?? '',
    avatarUrl: clerkUser.imageUrl,
  });
}

// Blocks with a clean 401 JSON response if there's no valid session.
// Deliberately NOT Clerk's own requireAuth() — see the note above.
async function requireAuthentication(req, res, next) {
  const auth = getAuth(req);
  if (!auth.userId) {
    return res.status(401).json({ error: 'Authentication required' });
  }
  try {
    req.user = await findOrCreateUser(auth.userId);
    next();
  } catch (err) {
    next(err);
  }
}

// Never blocks. Attaches req.user if signed in, leaves it undefined
// otherwise — for routes that must serve both anonymous and signed-in
// visitors (public schedule viewing).
async function attachUserIfPresent(req, res, next) {
  const auth = getAuth(req);
  if (!auth.userId) return next();
  try {
    req.user = await findOrCreateUser(auth.userId);
    next();
  } catch (err) {
    next(err);
  }
}

module.exports = { clerkAuth, requireAuthentication, attachUserIfPresent };