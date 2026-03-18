// middleware/validate.js
// Reusable Joi validation middleware.
// Usage: router.post('/greenhouses', validate(createGreenhouseSchema), handler)

const Joi = require('joi');
const { ValidationError } = require('../errors/httpErrors');

/**
 * @param {Joi.Schema} schema  - Schema to validate against
 * @param {'body'|'query'|'params'} [target='body']
 */
function validate(schema, target = 'body') {
  return (req, res, next) => {
    const { error, value } = schema.validate(req[target], {
      abortEarly: false,   // collect ALL errors, not just the first
      stripUnknown: true,  // drop unknown keys silently
    });

    if (error) {
      const details = error.details.map((d) => ({
        field:   d.context?.key ?? null,
        message: d.message.replace(/['"]/g, ''), // strip Joi's surrounding quotes
      }));
      return next(ValidationError(details));
    }

    // Replace with sanitised value
    req[target] = value;
    next();
  };
}

module.exports = validate;
