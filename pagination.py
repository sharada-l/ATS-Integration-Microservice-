"""
pagination.py — Pagination utilities for the Greenhouse Harvest API.

Greenhouse uses page-number pagination with a per_page cap of 500.
There is no cursor or total-count header, so the end-of-results signal
is a response shorter than per_page.

Patterns implemented
─────────────────────
  paginate_all        Simple loop → flat list   (easiest, blocks until done)
  paginate_iter       Generator → one record at a time   (memory-efficient)
  paginate_pages      Generator → one raw page at a time (inspect metadata)
  paginate_parallel   Concurrent page fetches with ThreadPoolExecutor
  paginate_until      Stop early when a predicate is satisfied
  paginate_window     Date-windowed sweep for large time-range exports
  RateLimitPaginator  Retry-aware wrapper that respects 429 Retry-After

Run standalone to see all patterns in action:
    export GREENHOUSE_API_KEY="your_key"
    python pagination.py
"""

from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional

from ats_client import GreenhouseClient, RateLimitError

logger = logging.getLogger(__name__)

_DEFAULT_PER_PAGE = 100   # safe default — max Greenhouse allows is 500
_MAX_PER_PAGE     = 500
_MAX_WORKERS      = 5     # concurrent threads for parallel fetcher


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Page:
    """A single raw page of results plus metadata."""
    number:   int
    per_page: int
    records:  List[Dict]
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def is_last(self) -> bool:
        """True when this page has fewer records than per_page."""
        return self.count < self.per_page


@dataclass
class PaginationStats:
    """Counters collected during a full paginated sweep."""
    total_records: int = 0
    total_pages:   int = 0
    total_retries: int = 0
    elapsed_s:     float = 0.0

    def __str__(self) -> str:
        return (
            f"{self.total_records:,} records across {self.total_pages} pages "
            f"in {self.elapsed_s:.1f}s"
            + (f" ({self.total_retries} retries)" if self.total_retries else "")
        )


# ══════════════════════════════════════════════════════════════════════════════
# 1. PAGINATE ALL  — simplest: blocks until every page is fetched
# ══════════════════════════════════════════════════════════════════════════════

def paginate_all(
    client: GreenhouseClient,
    endpoint: str,
    per_page: int = _DEFAULT_PER_PAGE,
    max_records: Optional[int] = None,
    **filters: Any,
) -> List[Dict]:
    """
    Fetch every record from a paginated endpoint and return them as one list.

    Args:
        client:      GreenhouseClient instance.
        endpoint:    API path, e.g. "jobs", "candidates", "applications".
        per_page:    Records per page (1–500). Larger = fewer HTTP calls.
        max_records: Hard cap on records returned (None = no cap).
        **filters:   Extra query params forwarded to every request
                     (e.g. status="open", job_id=123).

    Returns:
        Flat list of all matching records.

    Examples:
        # All open jobs — one call per page, results combined
        all_jobs = paginate_all(client, "jobs", status="open")
        print(f"{len(all_jobs)} open jobs")

        # All active applications for a specific job, max 1 000
        apps = paginate_all(
            client,
            "applications",
            per_page=500,
            max_records=1_000,
            job_id=4567890,
            status="active",
        )
    """
    per_page = min(per_page, _MAX_PER_PAGE)
    results: List[Dict] = []
    page = 1

    while True:
        logger.debug("paginate_all: %s page=%d per_page=%d", endpoint, page, per_page)
        batch = client._get(endpoint, per_page=per_page, page=page, **filters)

        if not isinstance(batch, list):
            # Endpoint returned a single object, not a list
            return [batch]

        results.extend(batch)
        logger.info(
            "paginate_all: %s page=%d fetched=%d total=%d",
            endpoint, page, len(batch), len(results),
        )

        # Apply optional cap
        if max_records and len(results) >= max_records:
            logger.info("paginate_all: max_records=%d reached", max_records)
            return results[:max_records]

        # Greenhouse signals last page by returning fewer than per_page
        if len(batch) < per_page:
            break

        page += 1

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 2. PAGINATE ITER  — memory-efficient generator, one record at a time
# ══════════════════════════════════════════════════════════════════════════════

def paginate_iter(
    client: GreenhouseClient,
    endpoint: str,
    per_page: int = _DEFAULT_PER_PAGE,
    **filters: Any,
) -> Iterator[Dict]:
    """
    Yield one record at a time from a paginated endpoint.
    Only one page is held in memory at a time — ideal for large datasets
    or when processing records on-the-fly.

    Args:
        client:    GreenhouseClient instance.
        endpoint:  API path (e.g. "candidates").
        per_page:  Records per page (1–500).
        **filters: Extra query params forwarded to every request.

    Yields:
        Individual record dicts.

    Examples:
        # Stream every candidate, write to a file without loading all into RAM
        with open("candidates.jsonl", "w") as f:
            for candidate in paginate_iter(client, "candidates"):
                f.write(json.dumps(candidate) + "\\n")

        # Count hired applications without building a list
        hired = sum(
            1 for app in paginate_iter(client, "applications", status="hired")
        )
        print(f"{hired} hired applications")

        # Process records as they arrive
        for job in paginate_iter(client, "jobs", status="open", per_page=500):
            print(job["id"], job["name"])
    """
    per_page = min(per_page, _MAX_PER_PAGE)
    page = 1

    while True:
        logger.debug("paginate_iter: %s page=%d", endpoint, page)
        batch = client._get(endpoint, per_page=per_page, page=page, **filters)

        if not isinstance(batch, list):
            yield batch
            return

        yield from batch
        logger.debug("paginate_iter: %s page=%d yielded %d", endpoint, page, len(batch))

        if len(batch) < per_page:
            return   # last page

        page += 1


# ══════════════════════════════════════════════════════════════════════════════
# 3. PAGINATE PAGES  — yield raw Page objects for full control
# ══════════════════════════════════════════════════════════════════════════════

def paginate_pages(
    client: GreenhouseClient,
    endpoint: str,
    per_page: int = _DEFAULT_PER_PAGE,
    **filters: Any,
) -> Generator[Page, None, PaginationStats]:
    """
    Yield one Page object per API call, then return a PaginationStats summary.

    Use this when you need the raw page number, batch size, or per-page timing.

    Args:
        client:    GreenhouseClient instance.
        endpoint:  API path.
        per_page:  Records per page.
        **filters: Extra query params.

    Yields:
        Page objects (number, per_page, records, fetched_at, is_last).

    Returns (via StopIteration.value):
        PaginationStats with totals.

    Examples:
        # Log progress page-by-page
        gen = paginate_pages(client, "applications", status="active", per_page=100)
        try:
            while True:
                page = next(gen)
                print(f"Page {page.number}: {page.count} records  last={page.is_last}")
        except StopIteration as e:
            stats = e.value
            print(stats)   # "1 234 records across 13 pages in 4.2s"

        # Collect using a for-loop (stats available after loop)
        all_records = []
        for page in paginate_pages(client, "jobs"):
            all_records.extend(page.records)
            print(f"  page {page.number} → {page.count} jobs")
    """
    per_page = min(per_page, _MAX_PER_PAGE)
    stats    = PaginationStats()
    t_start  = time.monotonic()
    page_num = 1

    while True:
        logger.debug("paginate_pages: %s page=%d", endpoint, page_num)
        batch = client._get(endpoint, per_page=per_page, page=page_num, **filters)

        if not isinstance(batch, list):
            batch = [batch]

        page_obj = Page(number=page_num, per_page=per_page, records=batch)
        stats.total_records += page_obj.count
        stats.total_pages   += 1

        yield page_obj

        if page_obj.is_last:
            break

        page_num += 1

    stats.elapsed_s = time.monotonic() - t_start
    return stats


# ══════════════════════════════════════════════════════════════════════════════
# 4. PAGINATE PARALLEL  — fetch multiple pages concurrently
# ══════════════════════════════════════════════════════════════════════════════

def paginate_parallel(
    client: GreenhouseClient,
    endpoint: str,
    estimated_total: int,
    per_page: int = _MAX_PER_PAGE,
    max_workers: int = _MAX_WORKERS,
    **filters: Any,
) -> List[Dict]:
    """
    Fetch all pages concurrently using a thread pool.
    Dramatically faster for large datasets — use when you know the approximate
    total count (e.g. from a previous paginate_all call on a filtered subset).

    Args:
        client:          GreenhouseClient instance.
        endpoint:        API path.
        estimated_total: Approximate total records (used to pre-calculate pages).
                         Slight over-estimate is fine — empty pages are ignored.
        per_page:        Records per page (default: 500, the max).
        max_workers:     Concurrent threads (default: 5).
                         Keep low to avoid Greenhouse rate limits.
        **filters:       Extra query params.

    Returns:
        All records sorted by page order (not arrival order).

    Examples:
        # Export all 5 000 candidates — ~10 pages × 500 fetched in parallel
        candidates = paginate_parallel(
            client,
            "candidates",
            estimated_total=5_000,
            per_page=500,
            max_workers=4,
        )
        print(f"Fetched {len(candidates)} candidates")

        # Parallel fetch for a specific job's applications
        apps = paginate_parallel(
            client,
            "applications",
            estimated_total=800,
            job_id=4567890,
            status="active",
        )
    """
    per_page      = min(per_page, _MAX_PER_PAGE)
    num_pages     = math.ceil(estimated_total / per_page)
    page_results  = {}  # page_num → list of records

    def fetch_page(page_num: int) -> tuple[int, List[Dict]]:
        logger.debug("paginate_parallel: %s page=%d", endpoint, page_num)
        batch = client._get(endpoint, per_page=per_page, page=page_num, **filters)
        return page_num, (batch if isinstance(batch, list) else [batch])

    logger.info(
        "paginate_parallel: %s estimated_pages=%d workers=%d",
        endpoint, num_pages, max_workers,
    )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_page, p): p for p in range(1, num_pages + 1)}
        for future in as_completed(futures):
            page_num, records = future.result()
            page_results[page_num] = records
            logger.debug(
                "paginate_parallel: page=%d done records=%d", page_num, len(records)
            )

    # Reassemble in page order, skip empty trailing pages
    combined: List[Dict] = []
    for p in sorted(page_results):
        combined.extend(page_results[p])

    logger.info("paginate_parallel: %s total=%d", endpoint, len(combined))
    return combined


# ══════════════════════════════════════════════════════════════════════════════
# 5. PAGINATE UNTIL  — stop early when a predicate is satisfied
# ══════════════════════════════════════════════════════════════════════════════

def paginate_until(
    client: GreenhouseClient,
    endpoint: str,
    stop_when: Callable[[Dict], bool],
    per_page: int = _DEFAULT_PER_PAGE,
    inclusive: bool = True,
    **filters: Any,
) -> List[Dict]:
    """
    Collect records page-by-page and stop as soon as stop_when returns True
    for any record.

    Useful for time-ordered feeds where you want everything since a
    known checkpoint (e.g. "stop when created_at < last_sync_time").

    Args:
        client:     GreenhouseClient instance.
        endpoint:   API path.
        stop_when:  Callable that receives a record dict and returns True
                    when pagination should stop.
        per_page:   Records per page.
        inclusive:  If True, include the record that triggered the stop.
                    If False, exclude it.
        **filters:  Extra query params.

    Returns:
        Records collected up to (and optionally including) the stop record.

    Examples:
        # All applications created after a checkpoint date
        CHECKPOINT = "2024-06-01T00:00:00Z"

        def before_checkpoint(record: dict) -> bool:
            return record.get("created_at", "") < CHECKPOINT

        new_apps = paginate_until(
            client,
            "applications",
            stop_when=before_checkpoint,
            per_page=100,
            status="active",
        )
        print(f"{len(new_apps)} applications since {CHECKPOINT}")

        # Stop after finding the first rejected application
        seen = paginate_until(
            client,
            "applications",
            stop_when=lambda r: r.get("status") == "rejected",
            job_id=4567890,
        )
    """
    per_page = min(per_page, _MAX_PER_PAGE)
    collected: List[Dict] = []
    page = 1

    while True:
        batch = client._get(endpoint, per_page=per_page, page=page, **filters)
        if not isinstance(batch, list):
            batch = [batch]

        for record in batch:
            if stop_when(record):
                logger.info(
                    "paginate_until: stop triggered on page=%d total=%d",
                    page, len(collected),
                )
                if inclusive:
                    collected.append(record)
                return collected
            collected.append(record)

        if len(batch) < per_page:
            break

        page += 1

    return collected


# ══════════════════════════════════════════════════════════════════════════════
# 6. PAGINATE WINDOW  — sweep a date range in chunks to avoid huge pages
# ══════════════════════════════════════════════════════════════════════════════

def paginate_window(
    client: GreenhouseClient,
    endpoint: str,
    start: datetime,
    end: datetime,
    window_days: int = 30,
    per_page: int = _MAX_PER_PAGE,
    created_before_key: str = "created_before",
    created_after_key: str  = "created_after",
    **filters: Any,
) -> List[Dict]:
    """
    Sweep a date range in fixed-size windows, paginating within each window.

    Greenhouse list endpoints do not support server-side sorting, so large
    date ranges can return thousands of records per page. Splitting into
    windows keeps each paginate_all call small and avoids timeouts.

    Args:
        client:             GreenhouseClient instance.
        endpoint:           API path.
        start:              Start of the date range (inclusive).
        end:                End of the date range (inclusive).
        window_days:        Size of each window in days (default: 30).
        per_page:           Records per page within each window.
        created_before_key: Query param name for upper date bound.
        created_after_key:  Query param name for lower date bound.
        **filters:          Extra query params forwarded to every request.

    Returns:
        All records across all windows, deduplicated by "id".

    Examples:
        # All applications created in H1 2024, in 30-day windows
        from datetime import datetime, timezone

        apps = paginate_window(
            client,
            "applications",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 6, 30, tzinfo=timezone.utc),
            window_days=30,
        )
        print(f"{len(apps)} applications in H1 2024")

        # Smaller windows for high-volume endpoints
        candidates = paginate_window(
            client,
            "candidates",
            start=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, tzinfo=timezone.utc),
            window_days=7,   # weekly windows
        )
    """
    seen_ids: set = set()
    all_records: List[Dict] = []
    window_start = start

    while window_start < end:
        window_end = min(window_start + timedelta(days=window_days), end)
        iso = "%Y-%m-%dT%H:%M:%SZ"

        logger.info(
            "paginate_window: %s window %s → %s",
            endpoint,
            window_start.strftime(iso),
            window_end.strftime(iso),
        )

        window_records = paginate_all(
            client,
            endpoint,
            per_page=per_page,
            **{
                created_after_key:  window_start.strftime(iso),
                created_before_key: window_end.strftime(iso),
                **filters,
            },
        )

        new = 0
        for r in window_records:
            rid = r.get("id")
            if rid not in seen_ids:
                seen_ids.add(rid)
                all_records.append(r)
                new += 1

        logger.info(
            "paginate_window: window fetched=%d new=%d running_total=%d",
            len(window_records), new, len(all_records),
        )
        window_start = window_end

    return all_records


# ══════════════════════════════════════════════════════════════════════════════
# 7. RATE-LIMIT-AWARE PAGINATOR  — respects 429 Retry-After automatically
# ══════════════════════════════════════════════════════════════════════════════

class RateLimitPaginator:
    """
    Wraps any pagination call with automatic 429 back-off.

    Greenhouse enforces a per-minute rate limit. When a 429 response arrives,
    the Retry-After header says how many seconds to wait.
    This class catches RateLimitError and sleeps the correct amount
    before retrying — transparently to the caller.

    Args:
        client:       GreenhouseClient instance.
        max_retries:  Maximum 429 retries before giving up (default: 5).
        backoff_base: Extra seconds added to each retry delay (default: 2).
        on_retry:     Optional callback(retry_num, wait_s) called before each sleep.

    Examples:
        paginator = RateLimitPaginator(client, max_retries=5)

        # Use like paginate_all
        jobs = paginator.all("jobs", status="open")

        # Use like paginate_iter
        for candidate in paginator.iter("candidates"):
            process(candidate)

        # With a progress callback
        def on_retry(retry_num, wait_s):
            print(f"Rate limited — retry {retry_num} in {wait_s}s")

        paginator = RateLimitPaginator(client, on_retry=on_retry)
        apps = paginator.all("applications", status="active")
    """

    def __init__(
        self,
        client: GreenhouseClient,
        max_retries: int = 5,
        backoff_base: int = 2,
        on_retry: Optional[Callable[[int, float], None]] = None,
    ):
        self._client      = client
        self._max_retries = max_retries
        self._backoff     = backoff_base
        self._on_retry    = on_retry

    def _with_retry(self, fn: Callable, *args, **kwargs) -> Any:
        """Execute fn, sleeping on RateLimitError up to max_retries times."""
        for attempt in range(self._max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except RateLimitError as exc:
                if attempt == self._max_retries:
                    raise
                wait = exc.retry_after + self._backoff * attempt
                logger.warning(
                    "RateLimitPaginator: 429 received — sleeping %ds (attempt %d/%d)",
                    wait, attempt + 1, self._max_retries,
                )
                if self._on_retry:
                    self._on_retry(attempt + 1, wait)
                time.sleep(wait)

    def all(self, endpoint: str, **kwargs: Any) -> List[Dict]:
        """
        Rate-limit-aware version of paginate_all.

        Example:
            paginator = RateLimitPaginator(client)
            all_apps  = paginator.all("applications", status="active", per_page=500)
        """
        return self._with_retry(
            paginate_all, self._client, endpoint, **kwargs
        )

    def iter(self, endpoint: str, **kwargs: Any) -> Iterator[Dict]:
        """
        Rate-limit-aware version of paginate_iter.
        Note: rate-limit recovery happens per-page, not per-record.

        Example:
            paginator = RateLimitPaginator(client)
            for record in paginator.iter("candidates"):
                process(record)
        """
        per_page = min(kwargs.pop("per_page", _DEFAULT_PER_PAGE), _MAX_PER_PAGE)
        page = 1
        while True:
            def fetch(p):
                return self._client._get(endpoint, per_page=per_page, page=p, **kwargs)

            batch = self._with_retry(fetch, page)
            if not isinstance(batch, list):
                yield batch
                return
            yield from batch
            if len(batch) < per_page:
                return
            page += 1


# ══════════════════════════════════════════════════════════════════════════════
# STANDALONE DEMO  (python pagination.py)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    client = GreenhouseClient()   # reads GREENHOUSE_API_KEY from env

    print("\n" + "═" * 60)
    print("  Greenhouse Pagination Patterns")
    print("═" * 60)

    # ── 1. paginate_all ───────────────────────────────────────────────────────
    print("\n── 1. paginate_all  (flat list) ─────────────────────")
    jobs = paginate_all(client, "jobs", status="open", per_page=50)
    print(f"   {len(jobs)} open jobs total")
    if jobs:
        print(f"   First: [{jobs[0]['id']}] {jobs[0]['name']}")

    # ── 2. paginate_iter  (generator) ────────────────────────────────────────
    print("\n── 2. paginate_iter  (stream, count without storing) ─")
    count = 0
    for job in paginate_iter(client, "jobs", status="open", per_page=50):
        count += 1
    print(f"   Counted {count} open jobs (no list built)")

    # ── 3. paginate_pages  (page-by-page) ────────────────────────────────────
    print("\n── 3. paginate_pages  (raw pages + stats) ───────────")
    gen = paginate_pages(client, "jobs", status="open", per_page=50)
    try:
        while True:
            pg = next(gen)
            print(f"   page {pg.number}: {pg.count} records  last={pg.is_last}")
    except StopIteration as exc:
        print(f"   Stats: {exc.value}")

    # ── 4. paginate_until  (stop at checkpoint) ───────────────────────────────
    print("\n── 4. paginate_until  (stop at condition) ───────────")
    # Collect only the first 5 jobs (demo — use a real date check in production)
    collected = paginate_until(
        client,
        "jobs",
        stop_when=lambda r, _seen=[]: len(_seen) >= 4 or _seen.append(r) is None and False,
        per_page=50,
        status="open",
    )
    # Simpler real-world example using a date:
    # cutoff = "2024-01-01T00:00:00Z"
    # collected = paginate_until(
    #     client, "applications",
    #     stop_when=lambda r: r.get("created_at", "") < cutoff,
    #     status="active",
    # )
    print(f"   Collected {len(collected)} records before stop condition")

    # ── 5. paginate_window  (date windowing) ─────────────────────────────────
    print("\n── 5. paginate_window  (date-range sweep) ───────────")
    now   = datetime.now(timezone.utc)
    start = now - timedelta(days=90)
    apps  = paginate_window(
        client,
        "applications",
        start=start,
        end=now,
        window_days=30,
        per_page=500,
    )
    print(f"   {len(apps)} applications in the last 90 days (30-day windows)")

    # ── 6. RateLimitPaginator ─────────────────────────────────────────────────
    print("\n── 6. RateLimitPaginator  (auto retry on 429) ───────")

    def on_retry(attempt: int, wait_s: float):
        print(f"   ⚠ Rate limited — retry {attempt} in {wait_s:.0f}s")

    paginator = RateLimitPaginator(client, max_retries=3, on_retry=on_retry)
    all_jobs  = paginator.all("jobs", status="open", per_page=100)
    print(f"   Fetched {len(all_jobs)} jobs (with 429 protection)")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  All patterns completed.")
    print("═" * 60 + "\n")
