require('dotenv').config();
const express = require('express');
const mongoose = require('mongoose');
const connectDB = require('./config/db');

const app = express();
app.use(express.json());

connectDB();

// One test route — the whole point of Phase 2 is proving this exact
// chain works: server up, .env loaded, MongoDB actually connected.
// No business routes yet — those start in Phase 3.
app.get('/api/health', (req, res) => {
  res.json({
    status: 'ok',
    mongoConnected: mongoose.connection.readyState === 1,
  });
});

const PORT = process.env.PORT || 5000;
app.listen(PORT, () => {
  console.log(`MARSHAL express-api running on port ${PORT}`);
});