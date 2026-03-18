# 🌱 Greenhouse API

A RESTful API for managing greenhouse environments — monitor sensors, control climate zones, schedule irrigation, and track plant growth cycles.

---

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Server](#running-the-server)
- [API Reference](#api-reference)
  - [Authentication](#authentication)
  - [Greenhouses](#greenhouses)
  - [Zones](#zones)
  - [Sensors](#sensors)
  - [Schedules](#schedules)
- [Examples](#examples)
  - [Full Greenhouse Walkthrough](#full-greenhouse-walkthrough)
  - [Reading Sensor Data](#reading-sensor-data)
  - [Triggering Irrigation](#triggering-irrigation)
- [Error Handling](#error-handling)
- [Testing](#testing)
- [Contributing](#contributing)

---

## Overview

The Greenhouse API lets you programmatically manage one or more greenhouse facilities. Each greenhouse contains **zones** (growing areas), **sensors** (temperature, humidity, soil moisture, light), and **schedules** (irrigation, lighting, venting).

**Base URL:** `https://api.yourservice.com/v1`

---

## Prerequisites

| Requirement | Version |
|---|---|
| Node.js | ≥ 18.x |
| PostgreSQL | ≥ 14 |
| Redis | ≥ 7 (for job scheduling) |
| npm or yarn | latest stable |

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/your-org/greenhouse-api.git
cd greenhouse-api

# 2. Install dependencies
npm install

# 3. Copy the example environment file
cp .env.example .env

# 4. Run database migrations
npm run db:migrate

# 5. (Optional) Seed sample data
npm run db:seed
```

---

## Configuration

Edit `.env` with your values:

```env
# Server
PORT=3000
NODE_ENV=development

# Database
DATABASE_URL=postgresql://user:password@localhost:5432/greenhouse_db

# Redis (for scheduled jobs)
REDIS_URL=redis://localhost:6379

# Authentication
JWT_SECRET=your-super-secret-key
JWT_EXPIRES_IN=7d

# Sensor polling interval (seconds)
SENSOR_POLL_INTERVAL=60

# Alerts
ALERT_EMAIL=ops@yourcompany.com
SMTP_HOST=smtp.yourprovider.com
SMTP_PORT=587
SMTP_USER=your-smtp-user
SMTP_PASS=your-smtp-password
```

---

## Running the Server

```bash
# Development (with hot reload)
npm run dev

# Production
npm run build
npm start

# With Docker
docker compose up --build
```

Server starts at `http://localhost:3000` by default.

---

## API Reference

### Authentication

All endpoints require a Bearer token unless marked **public**.

#### `POST /v1/auth/login` — public

```json
// Request
{
  "email": "grower@example.com",
  "password": "s3cr3t!"
}

// Response 200
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_at": "2026-03-25T10:00:00Z"
}
```

Pass the token in subsequent requests:

```
Authorization: Bearer <token>
```

---

### Greenhouses

#### `GET /v1/greenhouses`

List all greenhouses the authenticated user has access to.

```json
// Response 200
{
  "data": [
    {
      "id": "gh_01J8KXYZ",
      "name": "North Wing",
      "location": "Block A, Row 3",
      "zone_count": 4,
      "status": "active",
      "created_at": "2025-11-01T08:00:00Z"
    }
  ],
  "meta": { "total": 1, "page": 1, "per_page": 20 }
}
```

#### `POST /v1/greenhouses`

Create a new greenhouse.

```json
// Request
{
  "name": "South Wing",
  "location": "Block B, Row 1",
  "target_temperature_c": 22,
  "target_humidity_pct": 70
}

// Response 201
{
  "id": "gh_02AABC12",
  "name": "South Wing",
  "location": "Block B, Row 1",
  "target_temperature_c": 22,
  "target_humidity_pct": 70,
  "status": "active",
  "created_at": "2026-03-18T09:15:00Z"
}
```

#### `GET /v1/greenhouses/:id`

Fetch a single greenhouse with current environment summary.

#### `PATCH /v1/greenhouses/:id`

Update greenhouse settings (name, targets, status).

#### `DELETE /v1/greenhouses/:id`

Soft-delete a greenhouse. Historical data is preserved.

---

### Zones

A zone is a subdivided area within a greenhouse (e.g., "Seedling Bench", "Tomato Row 2").

#### `GET /v1/greenhouses/:greenhouse_id/zones`
#### `POST /v1/greenhouses/:greenhouse_id/zones`
#### `GET /v1/greenhouses/:greenhouse_id/zones/:id`
#### `PATCH /v1/greenhouses/:greenhouse_id/zones/:id`
#### `DELETE /v1/greenhouses/:greenhouse_id/zones/:id`

**Zone object:**

```json
{
  "id": "zone_99XYZ",
  "greenhouse_id": "gh_01J8KXYZ",
  "name": "Tomato Row 2",
  "crop": "Solanum lycopersicum",
  "planted_at": "2026-01-10T00:00:00Z",
  "expected_harvest_at": "2026-04-10T00:00:00Z",
  "area_sqm": 12.5,
  "sensor_count": 3
}
```

---

### Sensors

#### `GET /v1/greenhouses/:greenhouse_id/sensors`

List all sensors in a greenhouse with their latest readings.

```json
// Response 200
{
  "data": [
    {
      "id": "sen_T01",
      "zone_id": "zone_99XYZ",
      "type": "temperature",
      "unit": "celsius",
      "latest_value": 21.4,
      "latest_reading_at": "2026-03-18T09:00:00Z",
      "status": "ok"
    },
    {
      "id": "sen_H01",
      "zone_id": "zone_99XYZ",
      "type": "humidity",
      "unit": "percent",
      "latest_value": 68.2,
      "latest_reading_at": "2026-03-18T09:00:00Z",
      "status": "ok"
    }
  ]
}
```

#### `GET /v1/sensors/:id/readings`

Fetch historical readings for a sensor. Supports time range filtering.

**Query params:**

| Param | Type | Description |
|---|---|---|
| `from` | ISO 8601 | Start of range (default: 24h ago) |
| `to` | ISO 8601 | End of range (default: now) |
| `interval` | string | Aggregation bucket: `1m`, `5m`, `1h`, `1d` |
| `limit` | integer | Max rows returned (default: 500) |

```bash
GET /v1/sensors/sen_T01/readings?from=2026-03-17T00:00:00Z&interval=1h
```

---

### Schedules

Schedules control automated irrigation, lighting, and venting.

#### `POST /v1/greenhouses/:greenhouse_id/schedules`

```json
// Request
{
  "zone_id": "zone_99XYZ",
  "action": "irrigate",
  "cron": "0 6 * * *",
  "duration_seconds": 120,
  "enabled": true
}

// Response 201
{
  "id": "sched_A1B2",
  "zone_id": "zone_99XYZ",
  "action": "irrigate",
  "cron": "0 6 * * *",
  "duration_seconds": 120,
  "next_run_at": "2026-03-19T06:00:00Z",
  "enabled": true
}
```

**Supported actions:** `irrigate`, `vent_open`, `vent_close`, `lights_on`, `lights_off`

#### `POST /v1/schedules/:id/trigger`

Manually trigger a scheduled action immediately.

---

## Examples

### Full Greenhouse Walkthrough

This example walks through setting up a new greenhouse from scratch.

**Step 1 — Log in and get your token:**

```bash
curl -s -X POST https://api.yourservice.com/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"grower@example.com","password":"s3cr3t!"}' \
  | jq -r '.token'
```

Save the token:

```bash
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

**Step 2 — Create the greenhouse:**

```bash
curl -s -X POST https://api.yourservice.com/v1/greenhouses \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "North Wing",
    "location": "Block A",
    "target_temperature_c": 22,
    "target_humidity_pct": 70
  }'
```

Save the greenhouse ID returned (e.g., `gh_01J8KXYZ`).

**Step 3 — Add a zone:**

```bash
curl -s -X POST https://api.yourservice.com/v1/greenhouses/gh_01J8KXYZ/zones \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Tomato Row 2",
    "crop": "Solanum lycopersicum",
    "planted_at": "2026-01-10T00:00:00Z",
    "area_sqm": 12.5
  }'
```

**Step 4 — Schedule daily irrigation at 6 AM:**

```bash
curl -s -X POST https://api.yourservice.com/v1/greenhouses/gh_01J8KXYZ/schedules \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "zone_id": "zone_99XYZ",
    "action": "irrigate",
    "cron": "0 6 * * *",
    "duration_seconds": 120,
    "enabled": true
  }'
```

---

### Reading Sensor Data

Fetch the last 24 hours of temperature data in 1-hour buckets:

```bash
curl -s "https://api.yourservice.com/v1/sensors/sen_T01/readings?interval=1h" \
  -H "Authorization: Bearer $TOKEN" | jq '.data'
```

**Sample response:**

```json
[
  { "timestamp": "2026-03-17T08:00:00Z", "avg": 21.1, "min": 20.8, "max": 21.6 },
  { "timestamp": "2026-03-17T09:00:00Z", "avg": 22.3, "min": 21.9, "max": 22.8 },
  { "timestamp": "2026-03-17T10:00:00Z", "avg": 23.0, "min": 22.5, "max": 23.4 }
]
```

---

### Triggering Irrigation

Manually run irrigation for a zone outside its schedule:

```bash
curl -s -X POST https://api.yourservice.com/v1/schedules/sched_A1B2/trigger \
  -H "Authorization: Bearer $TOKEN"

# Response 200
{
  "job_id": "job_XYZ123",
  "action": "irrigate",
  "zone_id": "zone_99XYZ",
  "duration_seconds": 120,
  "triggered_at": "2026-03-18T11:42:00Z",
  "status": "queued"
}
```

---

## Error Handling

All errors follow a consistent envelope:

```json
{
  "error": {
    "code": "SENSOR_NOT_FOUND",
    "message": "No sensor with id 'sen_ZZZ' exists.",
    "status": 404
  }
}
```

| HTTP Status | Meaning |
|---|---|
| `400` | Bad request / validation error |
| `401` | Missing or invalid token |
| `403` | Insufficient permissions |
| `404` | Resource not found |
| `409` | Conflict (e.g. duplicate name) |
| `422` | Unprocessable entity |
| `429` | Rate limit exceeded |
| `500` | Internal server error |

---

## Testing

```bash
# Run all tests
npm test

# Unit tests only
npm run test:unit

# Integration tests (requires running Postgres + Redis)
npm run test:integration

# Coverage report
npm run test:coverage
```

---

## Contributing

1. Fork the repo and create a feature branch: `git checkout -b feature/my-feature`
2. Follow the existing code style (`npm run lint`)
3. Write tests for new functionality
4. Open a pull request with a clear description of your changes

Please read [CONTRIBUTING.md](./CONTRIBUTING.md) for the full guidelines.

---

## License

MIT © Your Organization
