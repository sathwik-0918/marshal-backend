const mongoose = require('mongoose');

const userSchema = new mongoose.Schema(
  {
    clerkId: {
      type: String,
      required: true,
      unique: true, // unique: true already creates an index — no need to add index: true too
    },
    name: {
      type: String,
      required: true,
    },
    email: {
      type: String,
      required: true,
    },
    avatarUrl: String,
  },
  { timestamps: true }
);

module.exports = mongoose.model('User', userSchema);