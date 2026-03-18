"""
handler.py - AWS Lambda entry points for Greenhouse ATS Integration
"""

import json
import logging
from ats_client import GreenhouseClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

client = GreenhouseClient()


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


# ── Jobs ──────────────────────────────────────────────────────────────────────

def list_jobs(event, context):
    """GET /jobs  →  paginated list of open jobs."""
    try:
        params = event.get("queryStringParameters") or {}
        jobs = client.get_jobs(
            status=params.get("status", "open"),
            per_page=int(params.get("per_page", 50)),
            page=int(params.get("page", 1)),
        )
        return _response(200, {"jobs": jobs})
    except Exception as exc:
        logger.error("list_jobs failed: %s", exc)
        return _response(500, {"error": str(exc)})


def get_job(event, context):
    """GET /jobs/{job_id}  →  single job detail."""
    try:
        job_id = event["pathParameters"]["job_id"]
        job = client.get_job(job_id)
        return _response(200, job)
    except Exception as exc:
        logger.error("get_job failed: %s", exc)
        return _response(500, {"error": str(exc)})


# ── Candidates ────────────────────────────────────────────────────────────────

def list_candidates(event, context):
    """GET /candidates  →  paginated candidate list."""
    try:
        params = event.get("queryStringParameters") or {}
        candidates = client.get_candidates(
            per_page=int(params.get("per_page", 50)),
            page=int(params.get("page", 1)),
        )
        return _response(200, {"candidates": candidates})
    except Exception as exc:
        logger.error("list_candidates failed: %s", exc)
        return _response(500, {"error": str(exc)})


def create_candidate(event, context):
    """POST /candidates  →  create a new candidate + application."""
    try:
        body = json.loads(event.get("body") or "{}")
        candidate = client.create_candidate(body)
        return _response(201, candidate)
    except ValueError as exc:
        return _response(400, {"error": str(exc)})
    except Exception as exc:
        logger.error("create_candidate failed: %s", exc)
        return _response(500, {"error": str(exc)})


# ── Applications ──────────────────────────────────────────────────────────────

def list_applications(event, context):
    """GET /applications  →  list applications, optionally filtered by job."""
    try:
        params = event.get("queryStringParameters") or {}
        applications = client.get_applications(
            job_id=params.get("job_id"),
            status=params.get("status"),
            per_page=int(params.get("per_page", 50)),
            page=int(params.get("page", 1)),
        )
        return _response(200, {"applications": applications})
    except Exception as exc:
        logger.error("list_applications failed: %s", exc)
        return _response(500, {"error": str(exc)})


def advance_application(event, context):
    """POST /applications/{application_id}/advance  →  move to next stage."""
    try:
        application_id = event["pathParameters"]["application_id"]
        body = json.loads(event.get("body") or "{}")
        result = client.advance_application(
            application_id,
            from_stage_id=body.get("from_stage_id"),
        )
        return _response(200, result)
    except Exception as exc:
        logger.error("advance_application failed: %s", exc)
        return _response(500, {"error": str(exc)})


def reject_application(event, context):
    """POST /applications/{application_id}/reject  →  reject with reason."""
    try:
        application_id = event["pathParameters"]["application_id"]
        body = json.loads(event.get("body") or "{}")
        result = client.reject_application(
            application_id,
            rejection_reason_id=body.get("rejection_reason_id"),
            rejection_email_template_id=body.get("rejection_email_template_id"),
        )
        return _response(200, result)
    except Exception as exc:
        logger.error("reject_application failed: %s", exc)
        return _response(500, {"error": str(exc)})


# ── Scorecards ────────────────────────────────────────────────────────────────

def list_scorecards(event, context):
    """GET /applications/{application_id}/scorecards."""
    try:
        application_id = event["pathParameters"]["application_id"]
        scorecards = client.get_scorecards(application_id)
        return _response(200, {"scorecards": scorecards})
    except Exception as exc:
        logger.error("list_scorecards failed: %s", exc)
        return _response(500, {"error": str(exc)})


# ── Webhooks ──────────────────────────────────────────────────────────────────

def webhook(event, context):
    """POST /webhook  →  receive and dispatch Greenhouse webhook events."""
    try:
        payload = json.loads(event.get("body") or "{}")
        action = payload.get("action", "unknown")
        logger.info("Webhook received: action=%s", action)

        handlers = {
            "application_updated": _on_application_updated,
            "candidate_hired":     _on_candidate_hired,
            "prospect_created":    _on_prospect_created,
        }

        handler_fn = handlers.get(action)
        if handler_fn:
            handler_fn(payload)

        return _response(200, {"received": True, "action": action})
    except Exception as exc:
        logger.error("webhook failed: %s", exc)
        return _response(500, {"error": str(exc)})


def _on_application_updated(payload: dict):
    application = payload.get("payload", {}).get("application", {})
    logger.info("Application updated: id=%s", application.get("id"))


def _on_candidate_hired(payload: dict):
    candidate = payload.get("payload", {}).get("candidate", {})
    logger.info("Candidate hired: id=%s name=%s", candidate.get("id"), candidate.get("name"))


def _on_prospect_created(payload: dict):
    prospect = payload.get("payload", {}).get("prospect", {})
    logger.info("Prospect created: id=%s", prospect.get("id"))
