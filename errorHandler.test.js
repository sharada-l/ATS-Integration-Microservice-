// tests/errorHandler.test.js
// Jest + Supertest — exercises every error shape the API can return.

const request = require('supertest');
const app     = require('../app');

// ─── Helper ───────────────────────────────────────────────────────────────────

function expectErrorShape(res, status, code) {
  expect(res.status).toBe(status);
  expect(res.body).toHaveProperty('error');
  expect(res.body.error).toMatchObject({ code, status });
  expect(typeof res.body.error.message).toBe('string');
}

// ─── 400 Validation ───────────────────────────────────────────────────────────

describe('POST /v1/greenhouses — validation', () => {
  it('returns 400 VALIDATION_ERROR when required fields are missing', async () => {
    const res = await request(app)
      .post('/v1/greenhouses')
      .send({}) // empty body
      .set('Authorization', 'Bearer valid-test-token');

    expectErrorShape(res, 400, 'VALIDATION_ERROR');
    expect(Array.isArray(res.body.error.details)).toBe(true);

    // Each detail item has field + message
    res.body.error.details.forEach((d) => {
      expect(d).toHaveProperty('field');
      expect(d).toHaveProperty('message');
    });
  });

  it('returns 400 VALIDATION_ERROR for out-of-range temperature', async () => {
    const res = await request(app)
      .post('/v1/greenhouses')
      .send({
        name: 'Test GH',
        target_temperature_c: 999, // way too hot
        target_humidity_pct: 70,
      })
      .set('Authorization', 'Bearer valid-test-token');

    expectErrorShape(res, 400, 'VALIDATION_ERROR');
    const tempError = res.body.error.details.find((d) => d.field === 'target_temperature_c');
    expect(tempError).toBeDefined();
  });
});

// ─── 401 Unauthorized ─────────────────────────────────────────────────────────

describe('GET /v1/greenhouses — auth', () => {
  it('returns 401 UNAUTHORIZED when no token is provided', async () => {
    const res = await request(app).get('/v1/greenhouses');
    expectErrorShape(res, 401, 'UNAUTHORIZED');
  });

  it('returns 401 INVALID_TOKEN when token is expired', async () => {
    const res = await request(app)
      .get('/v1/greenhouses')
      .set('Authorization', 'Bearer expired.token.here');
    expectErrorShape(res, 401, 'INVALID_TOKEN');
  });
});

// ─── 404 Not Found ────────────────────────────────────────────────────────────

describe('GET /v1/greenhouses/:id — not found', () => {
  it('returns 404 GREENHOUSE_NOT_FOUND for a non-existent id', async () => {
    const res = await request(app)
      .get('/v1/greenhouses/gh_DOESNOTEXIST')
      .set('Authorization', 'Bearer valid-test-token');

    expectErrorShape(res, 404, 'GREENHOUSE_NOT_FOUND');
    expect(res.body.error.message).toContain('gh_DOESNOTEXIST');
  });
});

// ─── 404 Unknown route ────────────────────────────────────────────────────────

describe('Unknown route', () => {
  it('returns 404 NOT_FOUND for completely unknown paths', async () => {
    const res = await request(app).get('/v1/nonexistent');
    expectErrorShape(res, 404, 'NOT_FOUND');
  });
});

// ─── 409 Conflict ─────────────────────────────────────────────────────────────

describe('POST /v1/greenhouses — conflict', () => {
  it('returns 409 CONFLICT when greenhouse name already exists', async () => {
    // Create once
    await request(app)
      .post('/v1/greenhouses')
      .send({ name: 'North Wing', target_temperature_c: 22, target_humidity_pct: 70 })
      .set('Authorization', 'Bearer valid-test-token');

    // Try again with same name
    const res = await request(app)
      .post('/v1/greenhouses')
      .send({ name: 'North Wing', target_temperature_c: 22, target_humidity_pct: 70 })
      .set('Authorization', 'Bearer valid-test-token');

    expectErrorShape(res, 409, 'CONFLICT');
  });
});

// ─── 422 Invalid cron ─────────────────────────────────────────────────────────

describe('POST /v1/greenhouses/:id/schedules — invalid cron', () => {
  it('returns 422 INVALID_CRON_EXPRESSION for a bad cron string', async () => {
    const res = await request(app)
      .post('/v1/greenhouses/gh_EXISTING/schedules')
      .send({
        zone_id:          'zone_EXISTING',
        action:           'irrigate',
        cron:             'not-a-cron',
        duration_seconds: 120,
      })
      .set('Authorization', 'Bearer valid-test-token');

    expectErrorShape(res, 422, 'INVALID_CRON_EXPRESSION');
    expect(res.body.error.details).toMatchObject({ cron: 'not-a-cron' });
  });
});

// ─── 503 Sensor Offline ───────────────────────────────────────────────────────

describe('POST /v1/schedules/:id/trigger — sensor offline', () => {
  it('returns 503 SENSOR_OFFLINE when the zone sensor is not responding', async () => {
    const res = await request(app)
      .post('/v1/schedules/sched_OFFLINE_SENSOR/trigger')
      .set('Authorization', 'Bearer valid-test-token');

    expectErrorShape(res, 503, 'SENSOR_OFFLINE');
    expect(res.body.error.details).toHaveProperty('sensorId');
  });
});
