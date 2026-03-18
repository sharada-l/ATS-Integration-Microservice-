"""
handler.py — AWS Lambda entry points for the Greenhouse ATS integration.

Each function maps 1-to-1 with a route in serverless.yml.
All handlers follow the same contract:

  Input:  API Gateway HTTP API event dict  (event, context)
  Output: {"statusCode": int, "headers": {...}, "body": "<JSON string>"}

Curl examples are included in every docstring.
Run locally:  serverless invoke local --function <name> --data '<event json>'
"""

import json
import hmac
import hashlib
import logging
import os
from typing import Any, Dict, Optional

from ats_client import (
    GreenhouseClient,
    GreenhouseError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Shared client (reused across warm Lambda invocations) ─────────────────────
client = GreenhouseClient()

# ── Webhook secret (set in SSM / env for HMAC verification) ──────────────────
_WEBHOOK_SECRET = os.environ.get("GREENHOUSE_WEBHOOK_SECRET", "")


# ══════════════════════════════════════════════════════════════════════════════
# RESPONSE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _ok(body: Any, status: int = 200) -> dict:
    """Return a 200/201 JSON response."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }

def _err(status: int, message: str, detail: str = "") -> dict:
    """Return an error JSON response with a consistent shape."""
    payload: Dict[str, str] = {"error": message}
    if detail:
        payload["detail"] = detail
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }

def _qs(event: dict, key: str, default: Any = None) -> Any:
    """Safely read a query-string parameter."""
    return (event.get("queryStringParameters") or {}).get(key, default)

def _path(event: dict, key: str) -> str:
    """Read a path parameter; raises KeyError if missing."""
    return event["pathParameters"][key]

def _body(event: dict) -> dict:
    """Parse the JSON request body; returns {} on empty/missing body."""
    raw = event.get("body") or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from exc

def _handle_client_error(exc: Exception, fn_name: str) -> dict:
    """Map GreenhouseClient exceptions to HTTP responses and log them."""
    if isinstance(exc, NotFoundError):
        return _err(404, "Not found", str(exc))
    if isinstance(exc, ValidationError):
        return _err(422, "Validation failed", str(exc))
    if isinstance(exc, RateLimitError):
        return _err(429, "Rate limited", f"Retry after {exc.retry_after}s")
    if isinstance(exc, (ValueError, KeyError)):
        return _err(400, "Bad request", str(exc))
    logger.error("%s unhandled error: %s", fn_name, exc, exc_info=True)
    return _err(500, "Internal server error", str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# AUTH (Lambda request authorizer)
# ══════════════════════════════════════════════════════════════════════════════

def authorizer(event, context) -> dict:
    """
    Lambda request authorizer — validates the X-Api-Key header.
    Returns a simple boolean response (enableSimpleResponses: true).

    Serverless invokes this automatically before every protected route.
    The API key is stored in SSM and injected as INTERNAL_API_KEY at deploy time.
    """
    token   = (event.get("headers") or {}).get("x-api-key", "")
    valid   = token == os.environ.get("INTERNAL_API_KEY", "")
    logger.info("authorizer: valid=%s", valid)
    return {"isAuthorized": valid}


# ══════════════════════════════════════════════════════════════════════════════
# /jobs
# ══════════════════════════════════════════════════════════════════════════════

def list_jobs(event, context) -> dict:
    """
    GET /jobs
    List open jobs with optional filters and pagination.

    Query params:
      status        "open" | "closed" | "draft"   (default: "open")
      department_id integer
      office_id     integer
      page          integer  (default: 1)
      per_page      integer  (default: 50, max: 500)

    Response 200:
      { "jobs": [...], "page": 1, "per_page": 50, "count": 12 }

    Curl:
      curl -H "X-Api-Key: $KEY" \
           "$BASE/jobs?status=open&per_page=10&page=1"
    """
    try:
        jobs = client.get_jobs(
            status        = _qs(event, "status", "open"),
            department_id = _int_qs(event, "department_id"),
            office_id     = _int_qs(event, "office_id"),
            per_page      = int(_qs(event, "per_page", 50)),
            page          = int(_qs(event, "page", 1)),
        )
        return _ok({
            "jobs":     jobs,
            "page":     int(_qs(event, "page", 1)),
            "per_page": int(_qs(event, "per_page", 50)),
            "count":    len(jobs),
        })
    except Exception as exc:
        return _handle_client_error(exc, "list_jobs")


def get_job(event, context) -> dict:
    """
    GET /jobs/{job_id}
    Retrieve a single job with its hiring team, departments, and pipeline stages.

    Path params:
      job_id   integer

    Response 200:  full Greenhouse job object
    Response 404:  { "error": "Not found" }

    Curl:
      curl -H "X-Api-Key: $KEY" "$BASE/jobs/4567890"
    """
    try:
        job_id = int(_path(event, "job_id"))
        job    = client.get_job(job_id)
        return _ok(job)
    except Exception as exc:
        return _handle_client_error(exc, "get_job")


def create_job(event, context) -> dict:
    """
    POST /jobs
    Create a new job requisition from a template.

    Body (JSON):
      template_job_id     integer  (required)
      number_of_openings  integer  (default: 1)
      job_post_name       string
      department_id       integer
      office_ids          [integer]
      opening_ids         [string]

    Response 201:  newly created job object
    Response 400:  missing required fields

    Curl:
      curl -X POST -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
           -d '{"template_job_id": 111222, "job_post_name": "Senior Engineer", \
                "department_id": 42, "office_ids": [7]}' \
           "$BASE/jobs"
    """
    try:
        b = _body(event)
        if not b.get("template_job_id"):
            return _err(400, "Bad request", "template_job_id is required")

        job = client.create_job(
            template_job_id    = int(b["template_job_id"]),
            number_of_openings = int(b.get("number_of_openings", 1)),
            job_post_name      = b.get("job_post_name"),
            department_id      = b.get("department_id"),
            office_ids         = b.get("office_ids"),
            opening_ids        = b.get("opening_ids"),
        )
        logger.info("create_job: created id=%s", job.get("id"))
        return _ok(job, status=201)
    except Exception as exc:
        return _handle_client_error(exc, "create_job")


def update_job(event, context) -> dict:
    """
    PATCH /jobs/{job_id}
    Update mutable fields on a job.

    Body (JSON):  any subset of mutable fields
      name, notes, anywhere, requisition_id,
      team_and_responsibilities, how_to_sell_this_job,
      office_ids, department_id, custom_fields

    Response 200:  updated job object

    Curl:
      curl -X PATCH -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
           -d '{"name": "Staff Engineer", "office_ids": [9]}' \
           "$BASE/jobs/4567890"
    """
    try:
        job_id = int(_path(event, "job_id"))
        fields = _body(event)
        if not fields:
            return _err(400, "Bad request", "Request body must not be empty")
        updated = client.update_job(job_id, **fields)
        logger.info("update_job: updated id=%s", job_id)
        return _ok(updated)
    except Exception as exc:
        return _handle_client_error(exc, "update_job")


def delete_job(event, context) -> dict:
    """
    DELETE /jobs/{job_id}
    Close and permanently delete a job. Irreversible.

    Response 200:  { "deleted": true, "job_id": 4567890 }
    Response 404:  job not found

    Curl:
      curl -X DELETE -H "X-Api-Key: $KEY" "$BASE/jobs/4567890"
    """
    try:
        job_id = int(_path(event, "job_id"))
        client.delete_job(job_id)
        logger.info("delete_job: deleted id=%s", job_id)
        return _ok({"deleted": True, "job_id": job_id})
    except Exception as exc:
        return _handle_client_error(exc, "delete_job")


def list_job_stages(event, context) -> dict:
    """
    GET /jobs/{job_id}/stages
    Return the ordered pipeline stages for a job.

    Response 200:
      { "job_id": 4567890, "stages": [{"id": 101, "name": "Phone Screen"}, ...] }

    Curl:
      curl -H "X-Api-Key: $KEY" "$BASE/jobs/4567890/stages"
    """
    try:
        job_id = int(_path(event, "job_id"))
        stages = client.get_job_stages(job_id)
        return _ok({"job_id": job_id, "stages": stages})
    except Exception as exc:
        return _handle_client_error(exc, "list_job_stages")


# ══════════════════════════════════════════════════════════════════════════════
# /candidates
# ══════════════════════════════════════════════════════════════════════════════

def list_candidates(event, context) -> dict:
    """
    GET /candidates
    List candidates with optional filters and pagination.

    Query params:
      job_id          integer  — only candidates with an application for this job
      email           string   — exact email match
      tag             string   — filter by tag name
      created_before  string   — ISO-8601 datetime upper bound
      created_after   string   — ISO-8601 datetime lower bound
      page            integer  (default: 1)
      per_page        integer  (default: 50, max: 500)

    Response 200:
      { "candidates": [...], "page": 1, "per_page": 50, "count": 8 }

    Curl:
      curl -H "X-Api-Key: $KEY" \
           "$BASE/candidates?job_id=4567890&per_page=25"

      # Search by email:
      curl -H "X-Api-Key: $KEY" \
           "$BASE/candidates?email=jane%40example.com"
    """
    try:
        candidates = client.get_candidates(
            job_id         = _int_qs(event, "job_id"),
            email          = _qs(event, "email"),
            tag            = _qs(event, "tag"),
            created_before = _qs(event, "created_before"),
            created_after  = _qs(event, "created_after"),
            per_page       = int(_qs(event, "per_page", 50)),
            page           = int(_qs(event, "page", 1)),
        )
        return _ok({
            "candidates": candidates,
            "page":       int(_qs(event, "page", 1)),
            "per_page":   int(_qs(event, "per_page", 50)),
            "count":      len(candidates),
        })
    except Exception as exc:
        return _handle_client_error(exc, "list_candidates")


def get_candidate(event, context) -> dict:
    """
    GET /candidates/{candidate_id}
    Retrieve a single candidate with all applications, contact info,
    educations, employments, and custom fields.

    Response 200:  full Greenhouse candidate object
    Response 404:  candidate not found

    Curl:
      curl -H "X-Api-Key: $KEY" "$BASE/candidates/9876543"
    """
    try:
        candidate_id = int(_path(event, "candidate_id"))
        candidate    = client.get_candidate(candidate_id)
        return _ok(candidate)
    except Exception as exc:
        return _handle_client_error(exc, "get_candidate")


def create_candidate(event, context) -> dict:
    """
    POST /candidates
    Create a candidate and attach them to a job application in one call.

    Body (JSON):
      first_name      string    (required)
      last_name       string    (required)
      job_id          integer   (required)
      email           string
      phone           string
      company         string
      title           string
      linkedin_url    string
      tags            [string]
      source_id       integer
      recruiter_id    integer
      coordinator_id  integer
      custom_fields   { name_key: value }

    Response 201:  newly created candidate object (includes application)
    Response 400:  missing required fields or invalid JSON

    Curl:
      curl -X POST -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
           -d '{
             "first_name":   "Jane",
             "last_name":    "Smith",
             "job_id":       4567890,
             "email":        "jane.smith@example.com",
             "phone":        "+1-555-0100",
             "company":      "Acme Corp",
             "title":        "Senior Engineer",
             "linkedin_url": "https://linkedin.com/in/janesmith",
             "tags":         ["referral", "python"]
           }' \
           "$BASE/candidates"
    """
    try:
        b = _body(event)

        # Validate required fields
        missing = [f for f in ("first_name", "last_name", "job_id") if not b.get(f)]
        if missing:
            return _err(400, "Bad request", f"Missing required fields: {', '.join(missing)}")

        candidate = client.create_candidate(
            first_name     = b["first_name"],
            last_name      = b["last_name"],
            job_id         = int(b["job_id"]),
            email          = b.get("email"),
            phone          = b.get("phone"),
            company        = b.get("company"),
            title          = b.get("title"),
            linkedin_url   = b.get("linkedin_url"),
            tags           = b.get("tags"),
            source_id      = b.get("source_id"),
            recruiter_id   = b.get("recruiter_id"),
            coordinator_id = b.get("coordinator_id"),
            custom_fields  = b.get("custom_fields"),
        )
        logger.info(
            "create_candidate: candidate_id=%s application_id=%s job_id=%s",
            candidate.get("id"),
            (candidate.get("applications") or [{}])[0].get("id"),
            b["job_id"],
        )
        return _ok(candidate, status=201)
    except Exception as exc:
        return _handle_client_error(exc, "create_candidate")


def update_candidate(event, context) -> dict:
    """
    PATCH /candidates/{candidate_id}
    Update mutable fields on a candidate.

    Body (JSON):  any subset of:
      first_name, last_name, company, title,
      phone_numbers, email_addresses, social_media_addresses,
      website_addresses, addresses, tags, custom_fields,
      recruiter, coordinator

    Response 200:  updated candidate object

    Curl:
      curl -X PATCH -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
           -d '{"company": "New Corp", "title": "Staff Engineer", "tags": ["promoted"]}' \
           "$BASE/candidates/9876543"
    """
    try:
        candidate_id = int(_path(event, "candidate_id"))
        fields = _body(event)
        if not fields:
            return _err(400, "Bad request", "Request body must not be empty")
        updated = client.update_candidate(candidate_id, **fields)
        logger.info("update_candidate: id=%s", candidate_id)
        return _ok(updated)
    except Exception as exc:
        return _handle_client_error(exc, "update_candidate")


def delete_candidate(event, context) -> dict:
    """
    DELETE /candidates/{candidate_id}
    Anonymise and permanently delete a candidate (GDPR right-to-erasure).
    All PII is removed. This action cannot be undone.

    Response 200:  { "deleted": true, "candidate_id": 9876543 }
    Response 404:  candidate not found

    Curl:
      curl -X DELETE -H "X-Api-Key: $KEY" "$BASE/candidates/9876543"
    """
    try:
        candidate_id = int(_path(event, "candidate_id"))
        client.delete_candidate(candidate_id)
        logger.info("delete_candidate: anonymised id=%s", candidate_id)
        return _ok({"deleted": True, "candidate_id": candidate_id})
    except Exception as exc:
        return _handle_client_error(exc, "delete_candidate")


def add_candidate_note(event, context) -> dict:
    """
    POST /candidates/{candidate_id}/notes
    Add a plain-text note to a candidate's profile.

    Body (JSON):
      user_id     integer  (required) — Greenhouse user authoring the note
      body        string   (required) — note text, plain text only
      visibility  string   (default: "admin_only")
                  "admin_only" | "private" | "public"

    Response 201:  { "note": { "id": ..., "body": "...", "visibility": "..." } }
    Response 400:  missing required fields

    Curl:
      curl -X POST -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
           -d '{
             "user_id":    11223,
             "body":       "Strong distributed systems background.",
             "visibility": "admin_only"
           }' \
           "$BASE/candidates/9876543/notes"
    """
    try:
        candidate_id = int(_path(event, "candidate_id"))
        b = _body(event)

        missing = [f for f in ("user_id", "body") if not b.get(f)]
        if missing:
            return _err(400, "Bad request", f"Missing required fields: {', '.join(missing)}")

        note = client.add_candidate_note(
            candidate_id = candidate_id,
            user_id      = int(b["user_id"]),
            body         = b["body"],
            visibility   = b.get("visibility", "admin_only"),
        )
        logger.info("add_candidate_note: candidate_id=%s note_id=%s", candidate_id, note.get("id"))
        return _ok({"note": note}, status=201)
    except Exception as exc:
        return _handle_client_error(exc, "add_candidate_note")


# ══════════════════════════════════════════════════════════════════════════════
# /applications
# ══════════════════════════════════════════════════════════════════════════════

def list_applications(event, context) -> dict:
    """
    GET /applications
    List applications with optional filters and pagination.

    Query params:
      job_id          integer  — filter to a specific job
      status          string   — "active" | "rejected" | "hired" | "converted"
      stage_id        integer  — applications currently in this stage
      candidate_id    integer  — all applications for a single candidate
      created_before  string   — ISO-8601 upper bound
      created_after   string   — ISO-8601 lower bound
      page            integer  (default: 1)
      per_page        integer  (default: 50, max: 500)

    Response 200:
      { "applications": [...], "page": 1, "per_page": 50, "count": 23 }

    Curl:
      curl -H "X-Api-Key: $KEY" \
           "$BASE/applications?job_id=4567890&status=active&per_page=100"

      # All applications for a single candidate:
      curl -H "X-Api-Key: $KEY" \
           "$BASE/applications?candidate_id=9876543"
    """
    try:
        applications = client.get_applications(
            job_id         = _int_qs(event, "job_id"),
            status         = _qs(event, "status"),
            stage_id       = _int_qs(event, "stage_id"),
            candidate_id   = _int_qs(event, "candidate_id"),
            created_before = _qs(event, "created_before"),
            created_after  = _qs(event, "created_after"),
            per_page       = int(_qs(event, "per_page", 50)),
            page           = int(_qs(event, "page", 1)),
        )
        return _ok({
            "applications": applications,
            "page":         int(_qs(event, "page", 1)),
            "per_page":     int(_qs(event, "per_page", 50)),
            "count":        len(applications),
        })
    except Exception as exc:
        return _handle_client_error(exc, "list_applications")


def get_application(event, context) -> dict:
    """
    GET /applications/{application_id}
    Retrieve a single application with its current stage, status,
    source, answers, and rejection details.

    Response 200:  full Greenhouse application object
    Response 404:  application not found

    Curl:
      curl -H "X-Api-Key: $KEY" "$BASE/applications/11223344"
    """
    try:
        application_id = int(_path(event, "application_id"))
        application    = client.get_application(application_id)
        return _ok(application)
    except Exception as exc:
        return _handle_client_error(exc, "get_application")


def update_application(event, context) -> dict:
    """
    PATCH /applications/{application_id}
    Update an application's source, referrer, or custom fields.

    Body (JSON):
      source_id      integer
      referrer       { "type": "id"|"email", "value": <user_id or email> }
      custom_fields  { name_key: value }

    Response 200:  updated application object

    Curl:
      curl -X PATCH -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
           -d '{
             "source_id": 5,
             "referrer":  {"type": "email", "value": "recruiter@example.com"}
           }' \
           "$BASE/applications/11223344"
    """
    try:
        application_id = int(_path(event, "application_id"))
        b = _body(event)
        updated = client.update_application(
            application_id = application_id,
            source_id      = b.get("source_id"),
            referrer       = b.get("referrer"),
            custom_fields  = b.get("custom_fields"),
        )
        logger.info("update_application: id=%s", application_id)
        return _ok(updated)
    except Exception as exc:
        return _handle_client_error(exc, "update_application")


def advance_application(event, context) -> dict:
    """
    POST /applications/{application_id}/advance
    Move an application to the next stage in the pipeline.

    Body (JSON):
      from_stage_id   integer  (required if candidate has multiple active applications)

    Response 200:  application object with updated current_stage

    Curl:
      # Advance without specifying current stage:
      curl -X POST -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
           -d '{}' \
           "$BASE/applications/11223344/advance"

      # Advance from a specific stage:
      curl -X POST -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
           -d '{"from_stage_id": 101}' \
           "$BASE/applications/11223344/advance"
    """
    try:
        application_id = int(_path(event, "application_id"))
        b = _body(event)
        result = client.advance_application(
            application_id = application_id,
            from_stage_id  = b.get("from_stage_id"),
        )
        stage = (result.get("current_stage") or {}).get("name", "unknown")
        logger.info("advance_application: id=%s new_stage=%s", application_id, stage)
        return _ok(result)
    except Exception as exc:
        return _handle_client_error(exc, "advance_application")


def move_application(event, context) -> dict:
    """
    POST /applications/{application_id}/move
    Move an application to a specific pipeline stage (not just 'next').
    Use GET /jobs/{job_id}/stages to look up valid stage IDs.

    Body (JSON):
      stage_id        integer  (required) — target stage
      from_stage_id   integer  (required if candidate has multiple applications)

    Response 200:  application object with updated current_stage
    Response 400:  stage_id is missing

    Curl:
      curl -X POST -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
           -d '{"stage_id": 103}' \
           "$BASE/applications/11223344/move"
    """
    try:
        application_id = int(_path(event, "application_id"))
        b = _body(event)

        if not b.get("stage_id"):
            return _err(400, "Bad request", "stage_id is required")

        result = client.move_application(
            application_id = application_id,
            stage_id       = int(b["stage_id"]),
            from_stage_id  = b.get("from_stage_id"),
        )
        stage = (result.get("current_stage") or {}).get("name", "unknown")
        logger.info("move_application: id=%s target_stage=%s", application_id, stage)
        return _ok(result)
    except Exception as exc:
        return _handle_client_error(exc, "move_application")


def reject_application(event, context) -> dict:
    """
    POST /applications/{application_id}/reject
    Reject an application, optionally sending a rejection email.
    Use GET /rejection_reasons to look up valid reason IDs.

    Body (JSON):
      rejection_reason_id           integer
      rejection_email_template_id   integer
      send_email_at                 string  (ISO-8601, e.g. "2024-09-01T09:00:00Z")

    Response 200:  application object with status "rejected"

    Curl:
      # Reject with reason and immediate email:
      curl -X POST -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
           -d '{
             "rejection_reason_id":         1,
             "rejection_email_template_id": 77
           }' \
           "$BASE/applications/11223344/reject"

      # Reject silently (no email):
      curl -X POST -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
           -d '{}' \
           "$BASE/applications/11223344/reject"
    """
    try:
        application_id = int(_path(event, "application_id"))
        b = _body(event)
        result = client.reject_application(
            application_id              = application_id,
            rejection_reason_id         = b.get("rejection_reason_id"),
            rejection_email_template_id = b.get("rejection_email_template_id"),
            send_email_at               = b.get("send_email_at"),
        )
        logger.info(
            "reject_application: id=%s reason_id=%s",
            application_id, b.get("rejection_reason_id"),
        )
        return _ok(result)
    except Exception as exc:
        return _handle_client_error(exc, "reject_application")


def unreject_application(event, context) -> dict:
    """
    POST /applications/{application_id}/unreject
    Undo a rejection and return the application to active status.

    Body: empty / ignored

    Response 200:  application object with status "active"

    Curl:
      curl -X POST -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
           -d '{}' \
           "$BASE/applications/11223344/unreject"
    """
    try:
        application_id = int(_path(event, "application_id"))
        result = client.unreject_application(application_id)
        logger.info("unreject_application: id=%s", application_id)
        return _ok(result)
    except Exception as exc:
        return _handle_client_error(exc, "unreject_application")


# ── Application sub-resources ──────────────────────────────────────────────────

def list_scorecards(event, context) -> dict:
    """
    GET /applications/{application_id}/scorecards
    Return all interviewer scorecards for an application.
    Each scorecard includes interviewer, overall_recommendation, and attribute ratings.

    Recommendation values:
      "strong_yes" | "yes" | "mixed" | "no" | "strong_no"

    Response 200:
      { "application_id": 11223344, "scorecards": [...], "count": 3 }

    Curl:
      curl -H "X-Api-Key: $KEY" "$BASE/applications/11223344/scorecards"
    """
    try:
        application_id = int(_path(event, "application_id"))
        scorecards     = client.get_scorecards(application_id)
        return _ok({
            "application_id": application_id,
            "scorecards":     scorecards,
            "count":          len(scorecards),
        })
    except Exception as exc:
        return _handle_client_error(exc, "list_scorecards")


def list_interviews(event, context) -> dict:
    """
    GET /applications/{application_id}/interviews
    Return all scheduled interviews for an application.

    Response 200:
      { "application_id": 11223344, "interviews": [...], "count": 2 }

    Curl:
      curl -H "X-Api-Key: $KEY" "$BASE/applications/11223344/interviews"
    """
    try:
        application_id = int(_path(event, "application_id"))
        interviews     = client.get_interviews(application_id)
        return _ok({
            "application_id": application_id,
            "interviews":     interviews,
            "count":          len(interviews),
        })
    except Exception as exc:
        return _handle_client_error(exc, "list_interviews")


def list_offers(event, context) -> dict:
    """
    GET /applications/{application_id}/offers
    Return all offers for an application.

    Response 200:
      { "application_id": 11223344, "offers": [...], "count": 1 }

    Curl:
      curl -H "X-Api-Key: $KEY" "$BASE/applications/11223344/offers"
    """
    try:
        application_id = int(_path(event, "application_id"))
        offers         = client.get_offers(application_id)
        return _ok({
            "application_id": application_id,
            "offers":         offers,
            "count":          len(offers),
        })
    except Exception as exc:
        return _handle_client_error(exc, "list_offers")


# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOK  (HMAC-verified, no API key auth)
# ══════════════════════════════════════════════════════════════════════════════

def webhook(event, context) -> dict:
    """
    POST /webhook
    Receive and dispatch inbound Greenhouse webhook events.
    Greenhouse signs each request with an HMAC-SHA256 Signature header.

    Supported actions:
      application_updated   — application moved stage or field changed
      application_created   — new application submitted
      candidate_hired       — application marked as hired
      candidate_merged      — two candidate profiles merged
      prospect_created      — new prospect added

    Response 200:  { "received": true, "action": "<action>" }
    Response 401:  HMAC signature mismatch
    Response 400:  malformed JSON body

    Curl (test without HMAC — only works if GREENHOUSE_WEBHOOK_SECRET is unset):
      curl -X POST -H "Content-Type: application/json" \
           -d '{"action": "candidate_hired", "payload": {"candidate": {"id": 1}}}' \
           "$BASE/webhook"
    """
    try:
        raw_body = event.get("body") or ""

        # HMAC verification (skipped when secret not configured — dev only)
        if _WEBHOOK_SECRET:
            sig_header = (event.get("headers") or {}).get("signature", "")
            if not _verify_webhook_signature(raw_body, sig_header):
                logger.warning("webhook: HMAC verification failed")
                return _err(401, "Unauthorized", "Invalid webhook signature")

        payload = json.loads(raw_body) if raw_body else {}
        action  = payload.get("action", "unknown")
        logger.info("webhook: action=%s", action)

        _WEBHOOK_DISPATCH.get(action, _on_unknown)(payload)

        return _ok({"received": True, "action": action})
    except json.JSONDecodeError as exc:
        return _err(400, "Bad request", f"Invalid JSON: {exc}")
    except Exception as exc:
        logger.error("webhook unhandled error: %s", exc, exc_info=True)
        return _err(500, "Internal server error", str(exc))


def _verify_webhook_signature(body: str, signature: str) -> bool:
    """
    Verify a Greenhouse webhook HMAC-SHA256 signature.
    Greenhouse computes: HMAC-SHA256(secret, body) and sends it as hex in
    the Signature header.
    """
    if not signature:
        return False
    expected = hmac.new(
        _WEBHOOK_SECRET.encode(),
        body.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Webhook event handlers ─────────────────────────────────────────────────────

def _on_application_updated(payload: dict) -> None:
    app = payload.get("payload", {}).get("application", {})
    logger.info(
        "application_updated: id=%s status=%s stage=%s",
        app.get("id"),
        app.get("status"),
        (app.get("current_stage") or {}).get("name"),
    )

def _on_application_created(payload: dict) -> None:
    app  = payload.get("payload", {}).get("application", {})
    cand = payload.get("payload", {}).get("candidate", {})
    logger.info(
        "application_created: application_id=%s candidate_id=%s job_id=%s",
        app.get("id"),
        cand.get("id"),
        (app.get("jobs") or [{}])[0].get("id"),
    )

def _on_candidate_hired(payload: dict) -> None:
    cand = payload.get("payload", {}).get("candidate", {})
    app  = payload.get("payload", {}).get("application", {})
    logger.info(
        "candidate_hired: candidate_id=%s name=%s application_id=%s",
        cand.get("id"),
        cand.get("name"),
        app.get("id"),
    )

def _on_candidate_merged(payload: dict) -> None:
    data = payload.get("payload", {})
    logger.info(
        "candidate_merged: winner_id=%s loser_id=%s",
        data.get("winner_candidate_id"),
        data.get("loser_candidate_id"),
    )

def _on_prospect_created(payload: dict) -> None:
    prospect = payload.get("payload", {}).get("prospect", {})
    logger.info("prospect_created: id=%s", prospect.get("id"))

def _on_unknown(payload: dict) -> None:
    logger.warning("webhook: unrecognised action=%s", payload.get("action"))


# Dispatch table — maps action strings to handler functions
_WEBHOOK_DISPATCH = {
    "application_updated": _on_application_updated,
    "application_created": _on_application_created,
    "candidate_hired":     _on_candidate_hired,
    "candidate_merged":    _on_candidate_merged,
    "prospect_created":    _on_prospect_created,
}


# ══════════════════════════════════════════════════════════════════════════════
# PRIVATE UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def _int_qs(event: dict, key: str) -> Optional[int]:
    """Read an optional integer query-string parameter."""
    val = _qs(event, key)
    return int(val) if val is not None else None
