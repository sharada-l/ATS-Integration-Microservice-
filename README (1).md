# Greenhouse ATS Integration

Serverless AWS Lambda service that wraps the [Greenhouse Harvest API v1](https://developers.greenhouse.io/harvest.html), exposing REST endpoints for jobs, candidates, applications, scorecards, and inbound webhooks.

---

## Project Structure

```
greenhouse/
├── handler.py          # Lambda entry points (one function per route)
├── ats_client.py       # Greenhouse Harvest API client
├── serverless.yml      # Serverless Framework deployment config
├── requirements.txt    # Python runtime dependencies
└── README.md
```

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| Node.js | 18+ |
| Serverless Framework | 3.x |
| AWS CLI | configured with deploy permissions |

Install the Serverless Framework and the Python requirements plugin:

```bash
npm install -g serverless
npm install --save-dev serverless-python-requirements
```

---

## Configuration

Credentials are stored in **AWS SSM Parameter Store** — never in code or environment files.

| SSM Path | Description |
|----------|-------------|
| `/greenhouse/{stage}/api_key` | Greenhouse Harvest API key (SecureString) |
| `/greenhouse/{stage}/on_behalf_of` | Optional user ID for auditing (String) |

Create parameters for the `dev` stage:

```bash
aws ssm put-parameter \
  --name /greenhouse/dev/api_key \
  --value "YOUR_API_KEY" \
  --type SecureString

aws ssm put-parameter \
  --name /greenhouse/dev/on_behalf_of \
  --value "12345" \
  --type String
```

---

## Local Development

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Export credentials locally (for ad-hoc testing, not for deployment)
export GREENHOUSE_API_KEY="your_key_here"
export GREENHOUSE_ON_BEHALF_OF="12345"

# 4. Invoke a function locally via Serverless
serverless invoke local --function listJobs
```

---

## Deployment

```bash
# Deploy to dev (default)
serverless deploy

# Deploy to production
serverless deploy --stage prod --region us-east-1
```

After deployment the CLI prints the API Gateway base URL, e.g.:

```
https://abc123.execute-api.us-east-1.amazonaws.com
```

---

## API Reference

### Jobs

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/jobs` | List jobs (`?status=open&page=1&per_page=50`) |
| `GET` | `/jobs/{job_id}` | Get a single job |

### Candidates

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/candidates` | List candidates |
| `POST` | `/candidates` | Create a candidate + application |

**Create candidate body:**

```json
{
  "first_name": "Jane",
  "last_name": "Smith",
  "email_addresses": [{ "value": "jane@example.com", "type": "personal" }],
  "applications": [{ "job_id": 123456 }]
}
```

### Applications

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/applications` | List applications (`?job_id=&status=&page=&per_page=`) |
| `POST` | `/applications/{id}/advance` | Advance to next stage |
| `POST` | `/applications/{id}/reject` | Reject with optional reason |

**Reject body:**

```json
{
  "rejection_reason_id": "42",
  "rejection_email_template_id": "7"
}
```

### Scorecards

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/applications/{id}/scorecards` | List scorecards for an application |

### Webhooks

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/webhook` | Receive Greenhouse webhook events |

**Supported webhook actions:**

- `application_updated`
- `candidate_hired`
- `prospect_created`

Configure the webhook URL in Greenhouse under **Configure → Dev Center → Web Hooks**.

---

## Environment Variables

| Variable | Source | Description |
|----------|--------|-------------|
| `GREENHOUSE_API_KEY` | SSM SecureString | Harvest API key |
| `GREENHOUSE_ON_BEHALF_OF` | SSM String (optional) | Greenhouse user ID for audit trail |

---

## Error Handling

All Lambda functions return standard HTTP responses:

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Resource created |
| 400 | Bad request (missing required fields) |
| 500 | Upstream or internal error |

Errors from the Greenhouse API (4xx/5xx) are surfaced via `requests.HTTPError` and logged to CloudWatch before returning a 500 to the caller.

---

## Extending the Integration

1. Add new methods to `GreenhouseClient` in `ats_client.py`.
2. Add corresponding handler functions in `handler.py`.
3. Register the new Lambda function and HTTP event in `serverless.yml`.
4. Redeploy with `serverless deploy`.

---

## License

MIT
