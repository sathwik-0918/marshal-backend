require('dotenv').config(); // MUST stay the very first line — clerkAuth below reads
                             // process.env.CLERK_SECRET_KEY when it's constructed,
                             // so dotenv has to run before that require() resolves.
const express = require('express');
const cors = require('cors');
const mongoose = require('mongoose');
const connectDB = require('./config/db');
const { clerkAuth } = require('./middleware/auth');
const errorHandler = require('./middleware/errorHandler');
const routes = require('./routes');

const app = express();
connectDB();

app.use(cors({ origin: process.env.CLIENT_ORIGIN, credentials: true }));
app.use(express.json());
app.use(clerkAuth); // attaches req.auth if a session exists; never blocks by itself

app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', mongoConnected: mongoose.connection.readyState === 1 });
});

app.use('/api', routes);

app.use(errorHandler); // must stay LAST — only routes registered before this reach it

const PORT = process.env.PORT || 5000;
app.listen(PORT, () => {
  console.log(`MARSHAL express-api running on port ${PORT}`);
});