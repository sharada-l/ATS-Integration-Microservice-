// middleware/errorHandler.js
// Global Express error-handling middleware.
// Mount LAST in app.js: app.use(errorHandler)

const AppError = require('../errors/AppError');

/**
 * Serialise any error into a clean JSON envelope:
 *
 *  {
 *    "error": {
 *      "code":    "GREENHOUSE_NOT_FOUND",
 *      "message": "Greenhouse 'gh_BADID' not found",
 *      "status":  404,
 *      "details": null          // present only when non-null
 *    }
 *  }
 */
function errorHandler(err, req, res, next) { // eslint-disable-line no-unused-vars
  const isDev = process.env.NODE_ENV === 'development';

  // ── 1. Normalise to AppError ───────────────────────────────────────────────
  let error = err;

  if (!(err instanceof AppError)) {
    // JWT library errors
    if (err.name === 'JsonWebTokenError' || err.name === 'TokenExpiredError') {
      error = new AppError('Token is invalid or expired', 401, 'INVALID_TOKEN');
    }
    // Postgres unique constraint violation (pg / pg-promise)
    else if (err.code === '23505') {
      error = new AppError('A resource with these values already exists', 409, 'CONFLICT');
    }
    // Postgres foreign-key violation
    else if (err.code === '23503') {
      error = new AppError('Referenced resource does not exist', 422, 'FOREIGN_KEY_VIOLATION');
    }
    // Joi validation errors (if not already wrapped upstream)
    else if (err.isJoi) {
      error = new AppError(
        'Validation failed',
        400,
        'VALIDATION_ERROR',
        err.details.map((d) => ({ field: d.context?.key ?? null, message: d.message }))
      );
    }
    // Unknown — map to 500
    else {
      error = new AppError(
        isDev ? err.message : 'An unexpected error occurred',
        500,
        'INTERNAL_SERVER_ERROR'
      );
    }
  }

  // ── 2. Log ─────────────────────────────────────────────────────────────────
  if (error.status >= 500) {
    console.error('[ERROR]', {
      code:    error.code,
      message: error.message,
      path:    req.path,
      method:  req.method,
      stack:   isDev ? err.stack : undefined,
    });
  }

  // ── 3. Build response body ─────────────────────────────────────────────────
  const body = {
    error: {
      code:    error.code,
      message: error.message,
      status:  error.status,
    },
  };

  // Only include details when they exist
  if (error.details !== null && error.details !== undefined) {
    body.error.details = error.details;
  }

  // Only include stack trace in development
  if (isDev && error.status === 500) {
    body.error.stack = err.stack;
  }

  res.status(error.status).json(body);
}

module.exports = errorHandler;
