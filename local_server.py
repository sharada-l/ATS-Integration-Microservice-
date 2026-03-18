"""
local_server.py — Flask shim that runs all Lambda handlers locally.

Simulates API Gateway HTTP API events so handler.py works unchanged.
Requires: pip install flask requests

Usage:
    export GREENHOUSE_API_KEY="your_key_here"
    export GREENHOUSE_ON_BEHALF_OF="12345"   # optional
    export INTERNAL_API_KEY="dev-key-123"    # authorizer key for local testing
    python local_server.py

Server starts at: http://localhost:4000
All routes are open locally (authorizer is bypassed for convenience).
"""

import json
import os
from flask import Flask, request, Response
import handler

app = Flask(__name__)
BASE = ""  # no stage prefix locally


def _event(path_params: dict = None) -> dict:
    """
    Build a minimal API Gateway HTTP API event from the current Flask request.
    Mirrors the shape Lambda receives from API Gateway.
    """
    body = request.get_data(as_text=True) or None
    return {
        "httpMethod":            request.method,
        "path":                  request.path,
        "pathParameters":        path_params or {},
        "queryStringParameters": dict(request.args) or None,
        "headers":               dict(request.headers),
        "body":                  body,
        "isBase64Encoded":       False,
    }


def _flask_response(result: dict) -> Response:
    """Convert a Lambda response dict into a Flask Response."""
    return Response(
        result.get("body", ""),
        status=result.get("statusCode", 200),
        headers=result.get("headers", {"Content-Type": "application/json"}),
    )


# ── /jobs ──────────────────────────────────────────────────────────────────────

@app.route("/jobs", methods=["GET"])
def list_jobs():
    return _flask_response(handler.list_jobs(_event(), None))

@app.route("/jobs", methods=["POST"])
def create_job():
    return _flask_response(handler.create_job(_event(), None))

@app.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    return _flask_response(handler.get_job(_event({"job_id": job_id}), None))

@app.route("/jobs/<job_id>", methods=["PATCH"])
def update_job(job_id):
    return _flask_response(handler.update_job(_event({"job_id": job_id}), None))

@app.route("/jobs/<job_id>", methods=["DELETE"])
def delete_job(job_id):
    return _flask_response(handler.delete_job(_event({"job_id": job_id}), None))

@app.route("/jobs/<job_id>/stages", methods=["GET"])
def list_job_stages(job_id):
    return _flask_response(handler.list_job_stages(_event({"job_id": job_id}), None))


# ── /candidates ────────────────────────────────────────────────────────────────

@app.route("/candidates", methods=["GET"])
def list_candidates():
    return _flask_response(handler.list_candidates(_event(), None))

@app.route("/candidates", methods=["POST"])
def create_candidate():
    return _flask_response(handler.create_candidate(_event(), None))

@app.route("/candidates/<candidate_id>", methods=["GET"])
def get_candidate(candidate_id):
    return _flask_response(handler.get_candidate(_event({"candidate_id": candidate_id}), None))

@app.route("/candidates/<candidate_id>", methods=["PATCH"])
def update_candidate(candidate_id):
    return _flask_response(handler.update_candidate(_event({"candidate_id": candidate_id}), None))

@app.route("/candidates/<candidate_id>", methods=["DELETE"])
def delete_candidate(candidate_id):
    return _flask_response(handler.delete_candidate(_event({"candidate_id": candidate_id}), None))

@app.route("/candidates/<candidate_id>/notes", methods=["POST"])
def add_candidate_note(candidate_id):
    return _flask_response(handler.add_candidate_note(_event({"candidate_id": candidate_id}), None))


# ── /applications ──────────────────────────────────────────────────────────────

@app.route("/applications", methods=["GET"])
def list_applications():
    return _flask_response(handler.list_applications(_event(), None))

@app.route("/applications/<application_id>", methods=["GET"])
def get_application(application_id):
    return _flask_response(handler.get_application(_event({"application_id": application_id}), None))

@app.route("/applications/<application_id>", methods=["PATCH"])
def update_application(application_id):
    return _flask_response(handler.update_application(_event({"application_id": application_id}), None))

@app.route("/applications/<application_id>/advance", methods=["POST"])
def advance_application(application_id):
    return _flask_response(handler.advance_application(_event({"application_id": application_id}), None))

@app.route("/applications/<application_id>/move", methods=["POST"])
def move_application(application_id):
    return _flask_response(handler.move_application(_event({"application_id": application_id}), None))

@app.route("/applications/<application_id>/reject", methods=["POST"])
def reject_application(application_id):
    return _flask_response(handler.reject_application(_event({"application_id": application_id}), None))

@app.route("/applications/<application_id>/unreject", methods=["POST"])
def unreject_application(application_id):
    return _flask_response(handler.unreject_application(_event({"application_id": application_id}), None))

@app.route("/applications/<application_id>/scorecards", methods=["GET"])
def list_scorecards(application_id):
    return _flask_response(handler.list_scorecards(_event({"application_id": application_id}), None))

@app.route("/applications/<application_id>/interviews", methods=["GET"])
def list_interviews(application_id):
    return _flask_response(handler.list_interviews(_event({"application_id": application_id}), None))

@app.route("/applications/<application_id>/offers", methods=["GET"])
def list_offers(application_id):
    return _flask_response(handler.list_offers(_event({"application_id": application_id}), None))


# ── /webhook ───────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    return _flask_response(handler.webhook(_event(), None))


# ── Health check ───────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return Response(
        json.dumps({"status": "ok", "service": "greenhouse-ats-local"}),
        status=200,
        headers={"Content-Type": "application/json"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 4000))
    print(f"""
╔══════════════════════════════════════════════════════╗
║  Greenhouse ATS — local server                       ║
║  http://localhost:{port}                               ║
║                                                      ║
║  Set env vars before starting:                       ║
║    GREENHOUSE_API_KEY   — Harvest API key            ║
║    INTERNAL_API_KEY     — any string for local auth  ║
╚══════════════════════════════════════════════════════╝
""")
    app.run(host="0.0.0.0", port=port, debug=True)
