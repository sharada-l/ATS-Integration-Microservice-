"""
ats_client.py — Greenhouse Harvest API v1 client
Docs: https://developers.greenhouse.io/harvest.html

Usage examples at the bottom of this file (run: python ats_client.py).
"""

import os
import logging
from typing import Any, Dict, Iterator, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_BASE_URL     = "https://harvest.greenhouse.io/v1"
_TIMEOUT      = 15    # seconds per request
_MAX_PER_PAGE = 500


# ── Custom exceptions ──────────────────────────────────────────────────────────

class GreenhouseError(Exception):
    """Base error for all Greenhouse API failures."""

class RateLimitError(GreenhouseError):
    """Raised on HTTP 429; includes retry_after seconds."""
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Rate limited — retry after {retry_after}s")

class NotFoundError(GreenhouseError):
    """Raised on HTTP 404."""

class ValidationError(GreenhouseError):
    """Raised on HTTP 422 (unprocessable entity)."""


# ── Client ────────────────────────────────────────────────────────────────────

class GreenhouseClient:
    """
    Thread-safe, retry-capable client for the Greenhouse Harvest API.

    Authentication: HTTP Basic Auth — API key as username, empty password.
    The On-Behalf-Of header attributes actions to a specific Greenhouse user
    and is required for endpoints that create audit-log entries.

    Quick start:
        client = GreenhouseClient()          # reads env vars
        jobs   = client.get_jobs()
        print(jobs[0]["name"])

    Environment variables:
        GREENHOUSE_API_KEY        — Harvest API key (required)
        GREENHOUSE_ON_BEHALF_OF   — Greenhouse user ID for audit trail (optional)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        on_behalf_of: Optional[str] = None,
    ):
        self._api_key      = api_key or os.environ["GREENHOUSE_API_KEY"]
        self._on_behalf_of = on_behalf_of or os.environ.get("GREENHOUSE_ON_BEHALF_OF", "")
        self._session      = self._build_session()

    # ── Session setup ──────────────────────────────────────────────────────────

    def _build_session(self) -> requests.Session:
        """
        Build a requests.Session with:
          - Basic Auth (api_key, "")
          - On-Behalf-Of header when provided
          - Automatic retry with exponential back-off on transient errors
        """
        session = requests.Session()
        session.auth = (self._api_key, "")
        session.headers.update({"Content-Type": "application/json"})

        if self._on_behalf_of:
            session.headers.update({"On-Behalf-Of": self._on_behalf_of})

        retry = Retry(
            total=3,
            backoff_factor=0.5,                          # 0.5s → 1s → 2s
            status_forcelist=[500, 502, 503, 504],       # 429 handled manually
            allowed_methods=["GET", "POST", "PATCH", "DELETE"],
            raise_on_status=False,
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    # ── Low-level HTTP helpers ─────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return f"{_BASE_URL}/{path.lstrip('/')}"

    def _raise_for_status(self, resp: requests.Response) -> None:
        """Map HTTP error codes to typed exceptions."""
        if resp.status_code in (200, 201):
            return
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            raise RateLimitError(retry_after)
        if resp.status_code == 404:
            raise NotFoundError(f"Not found: {resp.url}")
        if resp.status_code == 422:
            raise ValidationError(resp.text)
        resp.raise_for_status()  # everything else → requests.HTTPError

    def _get(self, path: str, **params: Any) -> Any:
        """
        GET request. Keyword args become query-string parameters;
        None values are automatically stripped.

        Example:
            self._get("jobs", status="open", per_page=50, page=1)
            # → GET /v1/jobs?status=open&per_page=50&page=1
        """
        resp = self._session.get(
            self._url(path),
            params={k: v for k, v in params.items() if v is not None},
            timeout=_TIMEOUT,
        )
        self._raise_for_status(resp)
        return resp.json()

    def _post(self, path: str, body: Optional[dict] = None) -> Any:
        """
        POST request with JSON body.

        Example:
            self._post("candidates", {"first_name": "Jane", ...})
        """
        resp = self._session.post(
            self._url(path),
            json=body or {},
            timeout=_TIMEOUT,
        )
        self._raise_for_status(resp)
        return resp.json()

    def _patch(self, path: str, body: dict) -> Any:
        """
        PATCH request with JSON body.

        Example:
            self._patch("jobs/123", {"name": "Senior Engineer"})
        """
        resp = self._session.patch(
            self._url(path),
            json=body,
            timeout=_TIMEOUT,
        )
        self._raise_for_status(resp)
        return resp.json()

    def _delete(self, path: str) -> Any:
        """
        DELETE request. Greenhouse DELETE responses vary (some JSON, some empty).

        Example:
            self._delete("candidates/456")
        """
        resp = self._session.delete(self._url(path), timeout=_TIMEOUT)
        self._raise_for_status(resp)
        try:
            return resp.json()
        except Exception:
            return {"status": "deleted", "url": resp.url}

    def _paginate(self, path: str, **params: Any) -> Iterator[Dict]:
        """
        Auto-paginate a list endpoint, yielding one record at a time.
        Stops when a page returns fewer records than per_page.

        Example:
            for job in client._paginate("jobs", status="open"):
                print(job["name"])
        """
        per_page = params.pop("per_page", 100)
        page = 1
        while True:
            results = self._get(path, **params, per_page=per_page, page=page)
            if not isinstance(results, list):
                yield results
                break
            yield from results
            if len(results) < per_page:
                break
            page += 1

    # ══════════════════════════════════════════════════════════════════════════
    # JOBS
    # ══════════════════════════════════════════════════════════════════════════

    def get_jobs(
        self,
        status: str = "open",
        department_id: Optional[int] = None,
        office_id: Optional[int] = None,
        per_page: int = 50,
        page: int = 1,
    ) -> List[Dict]:
        """
        Return a paginated list of jobs.

        Args:
            status:        "open" | "closed" | "draft"  (default: "open")
            department_id: Filter to a specific department.
            office_id:     Filter to a specific office.
            per_page:      Records per page (max 500).
            page:          Page number (1-indexed).

        Returns:
            List of job objects.

        Example:
            jobs = client.get_jobs(status="open", department_id=12, per_page=25)
            for job in jobs:
                print(job["id"], job["name"])
        """
        return self._get(
            "jobs",
            status=status,
            department_id=department_id,
            office_id=office_id,
            per_page=per_page,
            page=page,
        )

    def get_all_jobs(self, status: str = "open", **filters: Any) -> List[Dict]:
        """
        Fetch every job across all pages and return as one flat list.

        Example:
            all_open = client.get_all_jobs()
            print(f"{len(all_open)} open jobs found")
        """
        return list(self._paginate("jobs", status=status, **filters))

    def get_job(self, job_id: int) -> Dict:
        """
        Return a single job by its ID.

        Returns:
            Job object including departments, offices, hiring team, custom fields.

        Example:
            job = client.get_job(4567890)
            print(job["name"], job["status"])
        """
        return self._get(f"jobs/{job_id}")

    def create_job(
        self,
        template_job_id: int,
        number_of_openings: int = 1,
        job_post_name: Optional[str] = None,
        department_id: Optional[int] = None,
        office_ids: Optional[List[int]] = None,
        opening_ids: Optional[List[str]] = None,
    ) -> Dict:
        """
        Create a new job from a template.

        Args:
            template_job_id:    ID of the template job to clone (required).
            number_of_openings: Number of open positions (default: 1).
            job_post_name:      Public display name for the job post.
            department_id:      Department to assign.
            office_ids:         List of office IDs.
            opening_ids:        Requisition IDs, one per opening.

        Returns:
            Newly created job object.

        Example:
            job = client.create_job(
                template_job_id=111222,
                number_of_openings=2,
                job_post_name="Senior Backend Engineer",
                department_id=42,
                office_ids=[7, 8],
            )
            print("Created job:", job["id"])
        """
        body: Dict[str, Any] = {
            "template_job_id":    template_job_id,
            "number_of_openings": number_of_openings,
        }
        if job_post_name: body["job_post_name"] = job_post_name
        if department_id: body["department_id"] = department_id
        if office_ids:    body["office_ids"]    = office_ids
        if opening_ids:   body["opening_ids"]   = opening_ids
        return self._post("jobs", body)

    def update_job(self, job_id: int, **fields: Any) -> Dict:
        """
        Update mutable fields on a job.

        Accepted fields: name, notes, anywhere, requisition_id,
        team_and_responsibilities, how_to_sell_this_job,
        office_ids, department_id, custom_fields.

        Example:
            client.update_job(
                4567890,
                name="Staff Backend Engineer",
                office_ids=[9],
            )
        """
        return self._patch(f"jobs/{job_id}", fields)

    def delete_job(self, job_id: int) -> Dict:
        """
        Close and delete a job. Irreversible.

        Example:
            client.delete_job(4567890)
        """
        return self._delete(f"jobs/{job_id}")

    def get_job_stages(self, job_id: int) -> List[Dict]:
        """
        Return the ordered pipeline stages for a job.

        Example:
            stages = client.get_job_stages(4567890)
            for s in stages:
                print(s["id"], s["name"])
            # → 101  Phone Screen
            # → 102  Technical Interview
            # → 103  Offer
        """
        return self._get(f"jobs/{job_id}/stages")

    # ══════════════════════════════════════════════════════════════════════════
    # CANDIDATES
    # ══════════════════════════════════════════════════════════════════════════

    def get_candidates(
        self,
        job_id: Optional[int] = None,
        email: Optional[str] = None,
        tag: Optional[str] = None,
        created_before: Optional[str] = None,
        created_after: Optional[str] = None,
        per_page: int = 50,
        page: int = 1,
    ) -> List[Dict]:
        """
        Return a paginated list of candidates with optional filters.

        Args:
            job_id:         Only candidates with an application for this job.
            email:          Exact email match.
            tag:            Filter by tag name.
            created_before: ISO-8601 upper bound, e.g. "2024-01-01T00:00:00Z".
            created_after:  ISO-8601 lower bound.
            per_page:       Max 500.
            page:           1-indexed page number.

        Example:
            candidates = client.get_candidates(email="jane@example.com")
            if candidates:
                print(candidates[0]["id"])
        """
        return self._get(
            "candidates",
            job_id=job_id,
            email=email,
            tag=tag,
            created_before=created_before,
            created_after=created_after,
            per_page=per_page,
            page=page,
        )

    def get_all_candidates(self, **filters: Any) -> List[Dict]:
        """
        Fetch all candidates across pages as a flat list.

        Example:
            tagged = client.get_all_candidates(tag="ml-2024")
            print(f"{len(tagged)} candidates tagged ml-2024")
        """
        return list(self._paginate("candidates", **filters))

    def get_candidate(self, candidate_id: int) -> Dict:
        """
        Return a single candidate by ID.

        Returns:
            Candidate object with applications, email_addresses,
            phone_numbers, educations, employments, and custom fields.

        Example:
            c = client.get_candidate(9876543)
            print(c["first_name"], c["last_name"])
            for app in c["applications"]:
                print("  App:", app["id"], "→", app["status"])
        """
        return self._get(f"candidates/{candidate_id}")

    def create_candidate(
        self,
        first_name: str,
        last_name: str,
        job_id: int,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        company: Optional[str] = None,
        title: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source_id: Optional[int] = None,
        recruiter_id: Optional[int] = None,
        coordinator_id: Optional[int] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """
        Create a candidate and attach them to a job application in one call.

        Args:
            first_name:     Candidate's first name (required).
            last_name:      Candidate's last name (required).
            job_id:         Job to create the application against (required).
            email:          Primary email address.
            phone:          Primary phone number.
            company:        Current employer.
            title:          Current job title.
            linkedin_url:   LinkedIn profile URL.
            tags:           List of tag strings.
            source_id:      Greenhouse source ID (look up via get_sources()).
            recruiter_id:   Greenhouse user ID for the recruiter.
            coordinator_id: Greenhouse user ID for the coordinator.
            custom_fields:  Dict of custom field name_key → value.

        Returns:
            Newly created candidate object with embedded application.

        Example:
            candidate = client.create_candidate(
                first_name    = "Jane",
                last_name     = "Smith",
                job_id        = 4567890,
                email         = "jane.smith@example.com",
                phone         = "+1-555-0100",
                company       = "Acme Corp",
                title         = "Senior Engineer",
                linkedin_url  = "https://linkedin.com/in/janesmith",
                tags          = ["referral", "python"],
                recruiter_id  = 11223,
            )
            print("Candidate ID:",   candidate["id"])
            print("Application ID:", candidate["applications"][0]["id"])
        """
        body: Dict[str, Any] = {
            "first_name":   first_name,
            "last_name":    last_name,
            "applications": [{"job_id": job_id}],
        }
        if email:          body["email_addresses"]      = [{"value": email,        "type": "personal"}]
        if phone:          body["phone_numbers"]        = [{"value": phone,        "type": "mobile"}]
        if company:        body["company"]              = company
        if title:          body["title"]                = title
        if linkedin_url:   body["social_media_addresses"] = [{"value": linkedin_url}]
        if tags:           body["tags"]                 = tags
        if source_id:      body["applications"][0]["source_id"] = source_id
        if recruiter_id:   body["recruiter"]            = {"id": recruiter_id}
        if coordinator_id: body["coordinator"]          = {"id": coordinator_id}
        if custom_fields:  body["custom_fields"]        = custom_fields

        return self._post("candidates", body)

    def update_candidate(self, candidate_id: int, **fields: Any) -> Dict:
        """
        Update mutable fields on an existing candidate.

        Accepted fields: first_name, last_name, company, title,
        phone_numbers, email_addresses, social_media_addresses,
        website_addresses, addresses, tags, custom_fields,
        recruiter, coordinator.

        Example:
            client.update_candidate(
                9876543,
                company = "New Corp",
                title   = "Staff Engineer",
                tags    = ["promoted"],
            )
        """
        return self._patch(f"candidates/{candidate_id}", fields)

    def delete_candidate(self, candidate_id: int) -> Dict:
        """
        Anonymise and delete a candidate (GDPR right-to-erasure).
        All PII is removed. Cannot be undone.

        Example:
            client.delete_candidate(9876543)
        """
        return self._delete(f"candidates/{candidate_id}")

    def add_candidate_note(
        self,
        candidate_id: int,
        user_id: int,
        body: str,
        visibility: str = "admin_only",
    ) -> Dict:
        """
        Add a text note to a candidate's profile.

        Args:
            candidate_id: Candidate to annotate.
            user_id:      Greenhouse user ID authoring the note.
            body:         Note text (plain text, no HTML).
            visibility:   "admin_only" | "private" | "public"

        Example:
            note = client.add_candidate_note(
                candidate_id = 9876543,
                user_id      = 11223,
                body         = "Strong distributed systems background.",
                visibility   = "admin_only",
            )
            print("Note ID:", note["id"])
        """
        return self._post(
            f"candidates/{candidate_id}/notes",
            {"user_id": user_id, "body": body, "visibility": visibility},
        )

    # ══════════════════════════════════════════════════════════════════════════
    # APPLICATIONS
    # ══════════════════════════════════════════════════════════════════════════

    def get_applications(
        self,
        job_id: Optional[int] = None,
        status: Optional[str] = None,
        stage_id: Optional[int] = None,
        candidate_id: Optional[int] = None,
        created_before: Optional[str] = None,
        created_after: Optional[str] = None,
        per_page: int = 50,
        page: int = 1,
    ) -> List[Dict]:
        """
        Return a paginated list of applications with optional filters.

        Args:
            job_id:         Limit to a specific job.
            status:         "active" | "rejected" | "hired" | "converted"
            stage_id:       Applications currently in this stage.
            candidate_id:   All applications for a single candidate.
            created_before: ISO-8601 upper bound on creation date.
            created_after:  ISO-8601 lower bound on creation date.
            per_page:       Max 500.
            page:           1-indexed.

        Example:
            active = client.get_applications(
                job_id   = 4567890,
                status   = "active",
                per_page = 100,
            )
            print(f"{len(active)} active applications")
        """
        return self._get(
            "applications",
            job_id=job_id,
            status=status,
            stage_id=stage_id,
            candidate_id=candidate_id,
            created_before=created_before,
            created_after=created_after,
            per_page=per_page,
            page=page,
        )

    def get_all_applications(self, **filters: Any) -> List[Dict]:
        """
        Fetch all applications across pages as a flat list.

        Example:
            hired = client.get_all_applications(job_id=4567890, status="hired")
        """
        return list(self._paginate("applications", **filters))

    def get_application(self, application_id: int) -> Dict:
        """
        Return a single application by ID.

        Returns:
            Application with current_stage, status, source,
            answers, prospect flag, and rejection details.

        Example:
            app = client.get_application(11223344)
            print(app["status"])                    # "active"
            print(app["current_stage"]["name"])     # "Technical Interview"
        """
        return self._get(f"applications/{application_id}")

    def update_application(
        self,
        application_id: int,
        source_id: Optional[int] = None,
        referrer: Optional[Dict] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """
        Update an application's source, referrer, or custom fields.

        Args:
            source_id:    Greenhouse source ID.
            referrer:     {"type": "id",    "value": <user_id>} or
                          {"type": "email", "value": "name@example.com"}
            custom_fields: Dict of field name_key → value.

        Example:
            client.update_application(
                11223344,
                source_id = 5,
                referrer  = {"type": "email", "value": "recruiter@example.com"},
            )
        """
        body: Dict[str, Any] = {}
        if source_id:     body["source_id"]    = source_id
        if referrer:      body["referrer"]      = referrer
        if custom_fields: body["custom_fields"] = custom_fields
        return self._patch(f"applications/{application_id}", body)

    def advance_application(
        self,
        application_id: int,
        from_stage_id: Optional[int] = None,
    ) -> Dict:
        """
        Move an application to the next stage in the pipeline.

        Args:
            application_id: Application to advance.
            from_stage_id:  Current stage ID. Required when a candidate has
                            multiple active applications on the same job.

        Example:
            result = client.advance_application(11223344, from_stage_id=101)
            print("Now in:", result["current_stage"]["name"])
        """
        body: Dict[str, Any] = {}
        if from_stage_id:
            body["from_stage_id"] = from_stage_id
        return self._post(f"applications/{application_id}/advance", body)

    def move_application(
        self,
        application_id: int,
        stage_id: int,
        from_stage_id: Optional[int] = None,
    ) -> Dict:
        """
        Move an application to a specific pipeline stage (not just 'next').

        Args:
            application_id: Application to move.
            stage_id:       Target stage ID (from get_job_stages).
            from_stage_id:  Required when the candidate has multiple applications.

        Example:
            stages      = client.get_job_stages(4567890)
            offer_stage = next(s for s in stages if s["name"] == "Offer")
            client.move_application(11223344, stage_id=offer_stage["id"])
        """
        body: Dict[str, Any] = {"stage_id": stage_id}
        if from_stage_id:
            body["from_stage_id"] = from_stage_id
        return self._post(f"applications/{application_id}/move", body)

    def reject_application(
        self,
        application_id: int,
        rejection_reason_id: Optional[int] = None,
        rejection_email_template_id: Optional[int] = None,
        send_email_at: Optional[str] = None,
    ) -> Dict:
        """
        Reject an application, optionally sending a rejection email.

        Args:
            application_id:              Application to reject.
            rejection_reason_id:         Greenhouse rejection reason ID.
            rejection_email_template_id: Email template ID to send.
            send_email_at:               Schedule email delivery (ISO-8601).

        Example:
            reasons = client.get_rejection_reasons()
            reason  = next(r for r in reasons if r["name"] == "Withdrew")
            client.reject_application(
                application_id      = 11223344,
                rejection_reason_id = reason["id"],
                rejection_email_template_id = 77,
            )
        """
        body: Dict[str, Any] = {}
        if rejection_reason_id:
            body["rejection_reason"] = {"id": rejection_reason_id}
        if rejection_email_template_id:
            body["rejection_email_template"] = {"id": rejection_email_template_id}
        if send_email_at:
            body["send_email_at"] = send_email_at
        return self._post(f"applications/{application_id}/reject", body)

    def unreject_application(self, application_id: int) -> Dict:
        """
        Undo a rejection and return the application to active status.

        Example:
            client.unreject_application(11223344)
        """
        return self._post(f"applications/{application_id}/unreject")

    # ── Application sub-resources ──────────────────────────────────────────────

    def get_scorecards(self, application_id: int) -> List[Dict]:
        """
        Return all scorecards submitted for an application.

        Each scorecard includes the interviewer, overall recommendation
        ("strong_yes" | "yes" | "mixed" | "no" | "strong_no"),
        and per-attribute ratings.

        Example:
            cards = client.get_scorecards(11223344)
            for card in cards:
                print(card["interviewer"]["name"], card["overall_recommendation"])
        """
        return self._get(f"applications/{application_id}/scorecards")

    def get_interviews(self, application_id: int) -> List[Dict]:
        """
        Return scheduled interviews for an application.

        Example:
            for iv in client.get_interviews(11223344):
                print(iv["name"], iv["start"]["date_time"])
        """
        return self._get(f"applications/{application_id}/scheduled_interviews")

    def get_offers(self, application_id: int) -> List[Dict]:
        """
        Return all offers for an application.

        Example:
            for offer in client.get_offers(11223344):
                print(offer["status"], offer["created_at"])
        """
        return self._get(f"applications/{application_id}/offers")

    # ══════════════════════════════════════════════════════════════════════════
    # USERS
    # ══════════════════════════════════════════════════════════════════════════

    def get_users(
        self,
        email: Optional[str] = None,
        per_page: int = 50,
        page: int = 1,
    ) -> List[Dict]:
        """
        Return Greenhouse users (recruiters, interviewers, coordinators).

        Example:
            users = client.get_users(email="recruiter@example.com")
            if users:
                print("Recruiter ID:", users[0]["id"])
        """
        return self._get("users", email=email, per_page=per_page, page=page)

    def get_user(self, user_id: int) -> Dict:
        """
        Return a single Greenhouse user by ID.

        Example:
            user = client.get_user(11223)
            print(user["name"], user["primary_email_address"])
        """
        return self._get(f"users/{user_id}")

    # ══════════════════════════════════════════════════════════════════════════
    # DEPARTMENTS & OFFICES
    # ══════════════════════════════════════════════════════════════════════════

    def get_departments(self) -> List[Dict]:
        """
        Return all departments in the organisation.

        Example:
            for dept in client.get_departments():
                print(dept["id"], dept["name"])
        """
        return self._get("departments")

    def get_offices(self) -> List[Dict]:
        """
        Return all offices.

        Example:
            for office in client.get_offices():
                print(office["id"], office["name"])
        """
        return self._get("offices")

    # ══════════════════════════════════════════════════════════════════════════
    # LOOKUP TABLES
    # ══════════════════════════════════════════════════════════════════════════

    def get_sources(self) -> List[Dict]:
        """
        Return all candidate sources (LinkedIn, Referral, Job Board, etc.).

        Example:
            sources  = client.get_sources()
            linkedin = next(s for s in sources if "LinkedIn" in s["name"])
            print("LinkedIn source ID:", linkedin["id"])
        """
        return self._get("sources")

    def get_rejection_reasons(self) -> List[Dict]:
        """
        Return all configured rejection reasons.

        Example:
            reasons = client.get_rejection_reasons()
            for r in reasons:
                print(r["id"], r["name"])
            # → 1  Withdrew
            # → 2  Underqualified
            # → 3  Hired elsewhere
        """
        return self._get("rejection_reasons")

    def get_custom_fields(self, field_type: str = "job") -> List[Dict]:
        """
        Return custom field definitions for a given object type.

        Args:
            field_type: "job" | "candidate" | "application" | "offer"

        Example:
            fields = client.get_custom_fields("candidate")
            for f in fields:
                print(f["name_key"], f["field_type"])
        """
        return self._get("custom_fields", field_type=field_type)

    # ══════════════════════════════════════════════════════════════════════════
    # CONVENIENCE HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    def find_candidate_by_email(self, email: str) -> Optional[Dict]:
        """
        Return the first candidate matching an email address, or None.

        Example:
            c = client.find_candidate_by_email("jane@example.com")
            if c:
                print("Found:", c["id"])
            else:
                print("Not found")
        """
        results = self.get_candidates(email=email, per_page=1)
        return results[0] if results else None

    def hire_application(
        self,
        application_id: int,
        start_date: Optional[str] = None,
    ) -> Dict:
        """
        Mark an application as hired.

        Args:
            application_id: Application to hire.
            start_date:     Expected start date ("YYYY-MM-DD").

        Example:
            client.hire_application(11223344, start_date="2024-09-01")
        """
        body: Dict[str, Any] = {}
        if start_date:
            body["start_date"] = start_date
        return self._post(f"applications/{application_id}/hire", body)

    def get_pipeline_summary(self, job_id: int) -> Dict[str, int]:
        """
        Return a count of active applications per stage for a job.

        Example:
            summary = client.get_pipeline_summary(4567890)
            for stage_name, count in summary.items():
                print(f"  {stage_name}: {count}")
            # → Phone Screen: 12
            # → Technical Interview: 5
            # → Offer: 2
        """
        apps        = self.get_all_applications(job_id=job_id, status="active")
        stages      = self.get_job_stages(job_id)
        stage_names = {s["id"]: s["name"] for s in stages}

        summary: Dict[str, int] = {name: 0 for name in stage_names.values()}
        for app in apps:
            stage = app.get("current_stage") or {}
            name  = stage_names.get(stage.get("id"), "Unknown")
            summary[name] = summary.get(name, 0) + 1
        return summary


# ══════════════════════════════════════════════════════════════════════════════
# USAGE EXAMPLES  (run: python ats_client.py)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    client = GreenhouseClient()   # reads GREENHOUSE_API_KEY from env

    # ── 1. List open jobs ──────────────────────────────────────────────────────
    print("\n── 1. Open jobs ─────────────────────────────")
    jobs = client.get_jobs(status="open", per_page=5)
    for job in jobs:
        print(f"  [{job['id']}] {job['name']}  ({job['status']})")

    # ── 2. Pipeline stages for the first job ──────────────────────────────────
    if jobs:
        job_id = jobs[0]["id"]
        print(f"\n── 2. Stages for job {job_id} ───────────────────")
        for s in client.get_job_stages(job_id):
            print(f"  [{s['id']}] {s['name']}")

    # ── 3. Create a candidate + application ───────────────────────────────────
    print("\n── 3. Create candidate ──────────────────────")
    if jobs:
        new_cand = client.create_candidate(
            first_name   = "Alex",
            last_name    = "Rivera",
            job_id       = jobs[0]["id"],
            email        = "alex.rivera@example.com",
            phone        = "+1-555-0199",
            company      = "Acme Corp",
            title        = "Senior Engineer",
            linkedin_url = "https://linkedin.com/in/alexrivera",
            tags         = ["referral", "backend"],
        )
        cand_id = new_cand["id"]
        app_id  = new_cand["applications"][0]["id"]
        print(f"  Candidate ID:   {cand_id}")
        print(f"  Application ID: {app_id}")

        # ── 4. Add a recruiter note ────────────────────────────────────────────
        print("\n── 4. Add note ──────────────────────────────")
        users = client.get_users()
        if users:
            note = client.add_candidate_note(
                candidate_id = cand_id,
                user_id      = users[0]["id"],
                body         = "Strong distributed systems background.",
                visibility   = "admin_only",
            )
            print(f"  Note ID: {note['id']}")

        # ── 5. Advance the application ─────────────────────────────────────────
        print("\n── 5. Advance application ───────────────────")
        advanced = client.advance_application(app_id)
        stage = (advanced.get("current_stage") or {}).get("name", "—")
        print(f"  Now in stage: {stage}")

        # ── 6. Move to a specific stage ────────────────────────────────────────
        print("\n── 6. Move to Offer stage ───────────────────")
        stages = client.get_job_stages(jobs[0]["id"])
        offer  = next((s for s in stages if "offer" in s["name"].lower()), None)
        if offer:
            client.move_application(app_id, stage_id=offer["id"])
            print(f"  Moved to: {offer['name']}")

        # ── 7. Pipeline summary ────────────────────────────────────────────────
        print("\n── 7. Pipeline summary ──────────────────────")
        for stage_name, count in client.get_pipeline_summary(jobs[0]["id"]).items():
            print(f"  {stage_name}: {count}")

        # ── 8. Find candidate by email ─────────────────────────────────────────
        print("\n── 8. Find by email ─────────────────────────")
        found = client.find_candidate_by_email("alex.rivera@example.com")
        if found:
            print(f"  Found: {found['first_name']} {found['last_name']} (ID {found['id']})")

        # ── 9. Reject then unreject ────────────────────────────────────────────
        print("\n── 9. Reject / unreject ─────────────────────")
        reasons = client.get_rejection_reasons()
        if reasons:
            client.reject_application(app_id, rejection_reason_id=reasons[0]["id"])
            print(f"  Rejected: {reasons[0]['name']}")
            client.unreject_application(app_id)
            print(f"  Unrejected — status: {client.get_application(app_id)['status']}")

    # ── 10. Lookup tables ──────────────────────────────────────────────────────
    print("\n── 10. Sources (first 5) ────────────────────")
    for s in client.get_sources()[:5]:
        print(f"  [{s['id']}] {s['name']}")

    print("\n── 11. Departments (first 5) ────────────────")
    for d in client.get_departments()[:5]:
        print(f"  [{d['id']}] {d['name']}")

    print("\nDone.")
