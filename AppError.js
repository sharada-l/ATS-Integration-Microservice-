// errors/AppError.js
// Base custom error class — all thrown errors in the app extend this

class AppError extends Error {
  /**
   * @param {string} message   - Human-readable description
   * @param {number} status    - HTTP status code
   * @param {string} code      - Machine-readable error code (SCREAMING_SNAKE_CASE)
   * @param {object} [details] - Optional extra context (field errors, metadata, etc.)
   */
  constructor(message, status, code, details = null) {
    super(message);
    this.name = 'AppError';
    this.status = status;
    this.code = code;
    this.details = details;
    Error.captureStackTrace(this, this.constructor);
  }
}

module.exports = AppError;
