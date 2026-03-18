// errors/httpErrors.js
// Factory functions for every named error used across the greenhouse API.
// Import and throw these instead of raw `new Error(...)`.

const AppError = require('./AppError');

// ─── 400 Bad Request ──────────────────────────────────────────────────────────

const BadRequest = (message = 'Bad request', details = null) =>
  new AppError(message, 400, 'BAD_REQUEST', details);

/** Thrown by Joi / Zod validation middleware; details = array of field errors */
const ValidationError = (details) =>
  new AppError('Validation failed', 400, 'VALIDATION_ERROR', details);

// ─── 401 Unauthorized ─────────────────────────────────────────────────────────

const Unauthorized = (message = 'Authentication required') =>
  new AppError(message, 401, 'UNAUTHORIZED');

const InvalidToken = () =>
  new AppError('Token is invalid or expired', 401, 'INVALID_TOKEN');

// ─── 403 Forbidden ────────────────────────────────────────────────────────────

const Forbidden = (message = 'You do not have permission to perform this action') =>
  new AppError(message, 403, 'FORBIDDEN');

// ─── 404 Not Found ────────────────────────────────────────────────────────────

const NotFound = (resource = 'Resource') =>
  new AppError(`${resource} not found`, 404, 'NOT_FOUND');

// Greenhouse-domain 404s
const GreenhouseNotFound = (id) =>
  new AppError(`Greenhouse '${id}' not found`, 404, 'GREENHOUSE_NOT_FOUND');

const ZoneNotFound = (id) =>
  new AppError(`Zone '${id}' not found`, 404, 'ZONE_NOT_FOUND');

const SensorNotFound = (id) =>
  new AppError(`Sensor '${id}' not found`, 404, 'SENSOR_NOT_FOUND');

const ScheduleNotFound = (id) =>
  new AppError(`Schedule '${id}' not found`, 404, 'SCHEDULE_NOT_FOUND');

// ─── 409 Conflict ─────────────────────────────────────────────────────────────

const Conflict = (message = 'Resource already exists') =>
  new AppError(message, 409, 'CONFLICT');

// ─── 422 Unprocessable ────────────────────────────────────────────────────────

const UnprocessableEntity = (message, details = null) =>
  new AppError(message, 422, 'UNPROCESSABLE_ENTITY', details);

/** Sensor reading is outside the valid hardware range */
const SensorValueOutOfRange = (sensorId, value, min, max) =>
  new AppError(
    `Sensor '${sensorId}' reported value ${value}, which is outside the valid range [${min}, ${max}]`,
    422,
    'SENSOR_VALUE_OUT_OF_RANGE',
    { sensorId, value, min, max }
  );

/** Schedule cron expression is not parseable */
const InvalidCronExpression = (cron) =>
  new AppError(
    `'${cron}' is not a valid cron expression`,
    422,
    'INVALID_CRON_EXPRESSION',
    { cron }
  );

// ─── 429 Too Many Requests ────────────────────────────────────────────────────

const RateLimitExceeded = (retryAfterSeconds) =>
  new AppError('Rate limit exceeded', 429, 'RATE_LIMIT_EXCEEDED', {
    retry_after_seconds: retryAfterSeconds,
  });

// ─── 503 Service Unavailable ──────────────────────────────────────────────────

/** Sensor device is offline / not responding */
const SensorOffline = (sensorId) =>
  new AppError(
    `Sensor '${sensorId}' is offline and not returning readings`,
    503,
    'SENSOR_OFFLINE',
    { sensorId }
  );

module.exports = {
  BadRequest,
  ValidationError,
  Unauthorized,
  InvalidToken,
  Forbidden,
  NotFound,
  GreenhouseNotFound,
  ZoneNotFound,
  SensorNotFound,
  ScheduleNotFound,
  Conflict,
  UnprocessableEntity,
  SensorValueOutOfRange,
  InvalidCronExpression,
  RateLimitExceeded,
  SensorOffline,
};
