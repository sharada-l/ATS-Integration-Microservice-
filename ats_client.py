"""
ats_client.py - Thin wrapper around the Greenhouse Harvest API v1.
Docs: https://developers.greenhouse.io/harvest.html
"""

import os
import logging
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_BASE_URL = "https://harvest.greenhouse.io/v1"
_TIMEOUT = 15  # seconds


class GreenhouseClient:
    """Thread-safe, retry-capable client for the Greenhouse Harvest API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        on_behalf_of: Optional[str] = None,
    ):
        self._api_key = api_key or os.environ["GREENHOUSE_API_KEY"]
        self._on_behalf_of = on_behalf_of or os.environ.get("GREENHOUSE_ON_BEHALF_OF", "")
        self._session = self._build_session()

    # ── Session / HTTP ─────────────────────────────────────────────────────────

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.auth = (self._api_key, "")
        if self._on_behalf_of:
            session.headers.update({"On-Behalf-Of": self._on_behalf_of})

        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PATCH", "DELETE"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        return session

    def _get(self, path: str, **params) -> Any:
        url = f"{_BASE_URL}/{path.lstrip('/')}"
        resp = self._session.get(url, params={k: v for k, v in params.items() if v is not None}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> Any:
        url = f"{_BASE_URL}/{path.lstrip('/')}"
        resp = self._session.post(url, json=body, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, body: dict) -> Any:
        url = f"{_BASE_URL}/{path.lstrip('/')}"
        resp = self._session.patch(url, json=body, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # ── Jobs ───────────────────────────────────────────────────────────────────

    def get_jobs(
        self,
        status: str = "open",
        per_page: int = 50,
        page: int = 1,
    ) -> List[Dict]:
        """Return a list of jobs filtered by status."""
        return self._get("jobs", status=status, per_page=per_page, page=page)

    def get_job(self, job_id: str) -> Dict:
        """Return a single job by ID."""
        return self._get(f"jobs/{job_id}")

    # ── Candidates ─────────────────────────────────────────────────────────────

    def get_candidates(self, per_page: int = 50, page: int = 1) -> List[Dict]:
        """Return a paginated list of candidates."""
        return self._get("candidates", per_page=per_page, page=page)

    def get_candidate(self, candidate_id: str) -> Dict:
        """Return a single candidate by ID."""
        return self._get(f"candidates/{candidate_id}")

    def create_candidate(self, data: dict) -> Dict:
        """
        Create a candidate (and optionally an application).

        Required fields in *data*:
          - first_name (str)
          - last_name  (str)
          - applications: list[{job_id, ...}]

        Optional: email_addresses, phone_numbers, addresses, social_media_addresses,
                  website_addresses, tags, custom_fields, recruiter, coordinator.
        """
        required = ("first_name", "last_name", "applications")
        missing = [f for f in required if not data.get(f)]
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")
        return self._post("candidates", data)

    def update_candidate(self, candidate_id: str, data: dict) -> Dict:
        """Patch mutable fields on a candidate."""
        return self._patch(f"candidates/{candidate_id}", data)

    # ── Applications ───────────────────────────────────────────────────────────

    def get_applications(
        self,
        job_id: Optional[str] = None,
        status: Optional[str] = None,
        per_page: int = 50,
        page: int = 1,
    ) -> List[Dict]:
        """Return applications, optionally scoped to a job or status."""
        return self._get(
            "applications",
            job_id=job_id,
            status=status,
            per_page=per_page,
            page=page,
        )

    def get_application(self, application_id: str) -> Dict:
        """Return a single application by ID."""
        return self._get(f"applications/{application_id}")

    def advance_application(
        self,
        application_id: str,
        from_stage_id: Optional[str] = None,
    ) -> Dict:
        """Move an application to the next interview stage."""
        body = {}
        if from_stage_id:
            body["from_stage_id"] = from_stage_id
        return self._post(f"applications/{application_id}/advance", body)

    def reject_application(
        self,
        application_id: str,
        rejection_reason_id: Optional[str] = None,
        rejection_email_template_id: Optional[str] = None,
    ) -> Dict:
        """Reject an application with an optional reason and email template."""
        body: Dict[str, Any] = {}
        if rejection_reason_id:
            body["rejection_reason"] = {"id": rejection_reason_id}
        if rejection_email_template_id:
            body["rejection_email_template"] = {"id": rejection_email_template_id}
        return self._post(f"applications/{application_id}/reject", body)

    def move_application(self, application_id: str, stage_id: str) -> Dict:
        """Move an application to a specific stage (not just 'next')."""
        return self._post(
            f"applications/{application_id}/move",
            {"stage_id": stage_id},
        )

    # ── Scorecards ─────────────────────────────────────────────────────────────

    def get_scorecards(self, application_id: str) -> List[Dict]:
        """Return all scorecards for an application."""
        return self._get(f"applications/{application_id}/scorecards")

    # ── Scheduled Interviews ───────────────────────────────────────────────────

    def get_scheduled_interviews(self, application_id: str) -> List[Dict]:
        """Return scheduled interviews for an application."""
        return self._get(f"applications/{application_id}/scheduled_interviews")

    # ── Offers ─────────────────────────────────────────────────────────────────

    def get_offers(self, application_id: str) -> List[Dict]:
        """Return offers attached to an application."""
        return self._get(f"applications/{application_id}/offers")

    # ── Users ──────────────────────────────────────────────────────────────────

    def get_users(self, per_page: int = 50, page: int = 1) -> List[Dict]:
        """Return Greenhouse users (interviewers, recruiters, etc.)."""
        return self._get("users", per_page=per_page, page=page)

    # ── Departments & Offices ──────────────────────────────────────────────────

    def get_departments(self) -> List[Dict]:
        return self._get("departments")

    def get_offices(self) -> List[Dict]:
        return self._get("offices")
