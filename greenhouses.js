// routes/greenhouses.js
// Example route handlers showing how errors are thrown in practice.

const express = require('express');
const router = express.Router();
const Joi = require('joi');
const validate = require('../middleware/validate');
const {
  GreenhouseNotFound,
  ZoneNotFound,
  SensorNotFound,
  ScheduleNotFound,
  Conflict,
  ValidationError,
  InvalidCronExpression,
  SensorOffline,
} = require('../errors/httpErrors');

const db = require('../db'); // your DB client

// ─── Schemas ──────────────────────────────────────────────────────────────────

const createGreenhouseSchema = Joi.object({
  name:                  Joi.string().min(2).max(100).required(),
  location:              Joi.string().max(200).optional(),
  target_temperature_c:  Joi.number().min(-10).max(60).required(),
  target_humidity_pct:   Joi.number().min(0).max(100).required(),
});

const createScheduleSchema = Joi.object({
  zone_id:          Joi.string().required(),
  action:           Joi.string().valid('irrigate', 'vent_open', 'vent_close', 'lights_on', 'lights_off').required(),
  cron:             Joi.string().required(),
  duration_seconds: Joi.number().integer().min(1).max(3600).required(),
  enabled:          Joi.boolean().default(true),
});

// ─── GET /greenhouses ─────────────────────────────────────────────────────────

router.get('/', async (req, res, next) => {
  try {
    const rows = await db.query('SELECT * FROM greenhouses WHERE deleted_at IS NULL');
    res.json({ data: rows });
  } catch (err) {
    next(err); // passes to errorHandler
  }
});

// ─── POST /greenhouses ────────────────────────────────────────────────────────

router.post('/', validate(createGreenhouseSchema), async (req, res, next) => {
  try {
    const { name, location, target_temperature_c, target_humidity_pct } = req.body;

    // Check for duplicate name — throw Conflict if found
    const existing = await db.query('SELECT id FROM greenhouses WHERE name = $1', [name]);
    if (existing.length > 0) {
      return next(Conflict(`A greenhouse named '${name}' already exists`));
    }

    const [greenhouse] = await db.query(
      `INSERT INTO greenhouses (name, location, target_temperature_c, target_humidity_pct)
       VALUES ($1, $2, $3, $4) RETURNING *`,
      [name, location, target_temperature_c, target_humidity_pct]
    );

    res.status(201).json(greenhouse);
  } catch (err) {
    next(err);
  }
});

// ─── GET /greenhouses/:id ─────────────────────────────────────────────────────

router.get('/:id', async (req, res, next) => {
  try {
    const [greenhouse] = await db.query(
      'SELECT * FROM greenhouses WHERE id = $1 AND deleted_at IS NULL',
      [req.params.id]
    );

    if (!greenhouse) {
      return next(GreenhouseNotFound(req.params.id)); // → 404 GREENHOUSE_NOT_FOUND
    }

    res.json(greenhouse);
  } catch (err) {
    next(err);
  }
});

// ─── GET /greenhouses/:id/sensors ─────────────────────────────────────────────

router.get('/:id/sensors', async (req, res, next) => {
  try {
    const [greenhouse] = await db.query(
      'SELECT id FROM greenhouses WHERE id = $1 AND deleted_at IS NULL',
      [req.params.id]
    );
    if (!greenhouse) return next(GreenhouseNotFound(req.params.id));

    const sensors = await db.query(
      'SELECT * FROM sensors WHERE greenhouse_id = $1',
      [req.params.id]
    );

    // Flag any sensor that hasn't reported in 5 minutes as offline
    const now = Date.now();
    const enriched = sensors.map((s) => {
      const lastSeen = new Date(s.latest_reading_at).getTime();
      return {
        ...s,
        status: now - lastSeen > 5 * 60 * 1000 ? 'offline' : 'ok',
      };
    });

    res.json({ data: enriched });
  } catch (err) {
    next(err);
  }
});

// ─── POST /greenhouses/:id/schedules ─────────────────────────────────────────

router.post('/:id/schedules', validate(createScheduleSchema), async (req, res, next) => {
  try {
    const { zone_id, action, cron, duration_seconds, enabled } = req.body;

    // Validate zone belongs to this greenhouse
    const [zone] = await db.query(
      'SELECT id FROM zones WHERE id = $1 AND greenhouse_id = $2',
      [zone_id, req.params.id]
    );
    if (!zone) return next(ZoneNotFound(zone_id));

    // Validate the cron expression
    const cronParser = require('cron-parser');
    try {
      cronParser.parseExpression(cron);
    } catch {
      return next(InvalidCronExpression(cron)); // → 422 INVALID_CRON_EXPRESSION
    }

    const [schedule] = await db.query(
      `INSERT INTO schedules (greenhouse_id, zone_id, action, cron, duration_seconds, enabled)
       VALUES ($1, $2, $3, $4, $5, $6) RETURNING *`,
      [req.params.id, zone_id, action, cron, duration_seconds, enabled]
    );

    res.status(201).json(schedule);
  } catch (err) {
    next(err);
  }
});

// ─── POST /schedules/:id/trigger ─────────────────────────────────────────────

router.post('/schedules/:id/trigger', async (req, res, next) => {
  try {
    const [schedule] = await db.query('SELECT * FROM schedules WHERE id = $1', [req.params.id]);
    if (!schedule) return next(ScheduleNotFound(req.params.id));

    // Check the target sensor is online before dispatching
    const [sensor] = await db.query(
      'SELECT * FROM sensors WHERE zone_id = $1 AND type = $2',
      [schedule.zone_id, 'soil_moisture']
    );
    if (sensor) {
      const age = Date.now() - new Date(sensor.latest_reading_at).getTime();
      if (age > 5 * 60 * 1000) {
        return next(SensorOffline(sensor.id)); // → 503 SENSOR_OFFLINE
      }
    }

    const job = await scheduleQueue.add(schedule);
    res.json({ job_id: job.id, status: 'queued', triggered_at: new Date() });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
