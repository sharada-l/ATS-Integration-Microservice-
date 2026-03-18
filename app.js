// app.js
// Express entry point — shows exactly where error middleware is mounted.

const express = require('express');
const app = express();

app.use(express.json());

// ── Routes ────────────────────────────────────────────────────────────────────
app.use('/v1/greenhouses', require('./routes/greenhouses'));
// app.use('/v1/sensors',     require('./routes/sensors'));
// app.use('/v1/schedules',   require('./routes/schedules'));

// ── 404 catch-all (unknown routes) ───────────────────────────────────────────
app.use((req, res, next) => {
  const { NotFound } = require('./errors/httpErrors');
  next(NotFound(`Route ${req.method} ${req.path}`));
});

// ── Global error handler (MUST be last) ──────────────────────────────────────
app.use(require('./middleware/errorHandler'));

module.exports = app;
