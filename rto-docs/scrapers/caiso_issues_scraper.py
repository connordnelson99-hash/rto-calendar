#!/usr/bin/env python3
"""
CAISO Stakeholder Center initiatives scraper.

Two-pass:
  1. Fetch the index page (`stakeholdercenter.caiso.com`). The page inlines a
     `var currInitiatives = [...]` JS array containing every initiative as a
     structured record (Status, Phase, EIMCategories, StageA-D, etc.). Parse
     it directly.

  2. For each Active initiative, fetch the detail page
     `/RecurringStakeholderProcesses/{LinkTitle}` and harvest every
     `getCalendarEvent('activity-guid', 'evtId')` call. Each evtId resolves
     via `https://www.caiso.com/resources/export/event?id=<evtId>` to a
     meeting URL of the same shape we already store in `meetings.detail_url`.
     Cache event-id → url to avoid re-fetching across runs.

We only track meeting-level activity links, not the
`stakeholdercenter.caiso.com/InitiativeDocuments/` PDFs cited on the page.
The user can click through to the initiative page (or to the matched meeting
in our own UI) for materials.
"""

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import (
    get_connection, init_db,
    upsert_issue, upsert_issue_reference,
    resolve_issue_references, log_scrape,
    cache_caiso_event, get_cached_caiso_event,
)


class CAISOIssuesScraper:

    BASE_URL = "https://stakeholdercenter.caiso.com"
    INDEX_URL = "https://stakeholdercenter.caiso.com/"
    DETAIL_URL_TEMPLATE = "https://stakeholdercenter.caiso.com/RecurringStakeholderProcesses/{slug}"
    EVENT_API = "https://www.caiso.com/resources/export/event?id={eid}"
    REQUEST_DELAY = 0.5  # API is fast; keep polite

    rto_name = "CAISO"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self._last_request_time = 0.0

    # ── Public entrypoint ───────────────────────────────────────────────────

    def run(self, refresh_closed=False):
        """
        Discover initiatives, populate references for active ones, resolve
        URL matches against the existing meetings table.

        refresh_closed=False (default) skips detail-page fetches for
        Completed/Closed initiatives. They still get index metadata stored.
        """
        conn = get_connection()
        start = time.time()

        print(f"\n{'='*60}")
        print(f"  CAISO Stakeholder Initiatives Scraper")
        print(f"{'='*60}")

        try:
            html = self._get(self.INDEX_URL)
            initiatives = self._parse_index(html)
            print(f"  Index: {len(initiatives)} initiatives")

            active = [i for i in initiatives if i["status"] == "Active"]
            print(f"  Active (will fetch details): {len(active)}")

            for stub in initiatives:
                upsert_issue(
                    conn, self.rto_name, stub["native_id"],
                    url=stub["url"],
                    canonical_name=stub["canonical_name"],
                    short_title=stub["short_title"],
                    status=stub["status"],
                    phase=stub["phase"],
                    eim_categories=stub["eim_categories"],
                    committee_owner_label=stub["eim_categories"],
                    is_open=1 if stub["status"] == "Active" else 0,
                    initiated_date=stub["start_date"],
                    stage_a=stub["stage_a"],
                    stage_b=stub["stage_b"],
                    stage_c=stub["stage_c"],
                    stage_d=stub["stage_d"],
                )

            # Multiple "phases" can share an InitiativeId (and detail URL).
            # Dedupe so we only fetch the detail page once per unique GUID.
            seen_ids = set()
            targets = []
            for stub in (initiatives if refresh_closed else active):
                if stub["native_id"] in seen_ids:
                    continue
                seen_ids.add(stub["native_id"])
                targets.append(stub)
            ref_count = 0
            cached_hits = 0
            api_hits = 0

            for i, stub in enumerate(targets, 1):
                name = (stub["canonical_name"] or "")[:55]
                print(f"  [{i}/{len(targets)}] {name}", end=" ... ", flush=True)
                try:
                    detail_html = self._get(stub["url"])
                    activities = self._extract_activities(detail_html)

                    issue_id = conn.execute(
                        "SELECT id FROM issues WHERE rto=? AND native_id=?",
                        (self.rto_name, stub["native_id"]),
                    ).fetchone()["id"]

                    n_added = 0
                    for activity_guid, event_id in activities:
                        if not event_id:
                            continue
                        cached = get_cached_caiso_event(conn, event_id)
                        if cached and cached["url"]:
                            url = cached["url"]
                            title = cached["title"]
                            cached_hits += 1
                        else:
                            resolved = self._resolve_event(event_id)
                            if not resolved:
                                continue
                            url = resolved["url"]
                            title = resolved.get("title")
                            cache_caiso_event(
                                conn, event_id, url, title,
                                resolved.get("startDatePT"),
                            )
                            api_hits += 1
                        upsert_issue_reference(conn, issue_id, url, title)
                        n_added += 1
                        ref_count += 1

                    print(f"{n_added} activities ({len(activities)} found)")
                except Exception as e:
                    print(f"ERROR: {e}")

            stats = resolve_issue_references(conn)
            print(f"\n  Cache: {cached_hits} hits, {api_hits} API calls")
            print(f"  References: {stats['doc_matched']} doc-matched, "
                  f"{stats['meeting_matched']} meeting-matched, "
                  f"{stats['unmatched']} unmatched (external)")

            duration = time.time() - start
            log_scrape(
                conn, self.rto_name, "issues",
                self.INDEX_URL, "success",
                events_found=len(initiatives),
                docs_found=ref_count,
                duration_seconds=duration,
            )
            print(f"  Done in {duration:.1f}s")

        except Exception as e:
            duration = time.time() - start
            log_scrape(
                conn, self.rto_name, "issues",
                self.INDEX_URL, "error",
                error_message=str(e), duration_seconds=duration,
            )
            print(f"  ERROR: {e}")
            raise
        finally:
            conn.close()

    # ── Index parsing ──────────────────────────────────────────────────────

    _NET_DATE_RE = re.compile(r'"\\?/Date\(([^)]+)\)\\?/"')

    def _parse_index(self, html):
        """
        Pull `var currInitiatives = [...]` out of the inline script and
        normalize each record into our own dict shape.
        """
        soup = BeautifulSoup(html, "html.parser")
        # The biggest script tag holds the inline JSON dump.
        biggest = max(
            (s.string or "" for s in soup.find_all("script")),
            key=len, default="",
        )
        m = re.search(r'var\s+currInitiatives\s*=\s*(\[.*?\]);',
                      biggest, flags=re.DOTALL)
        if not m:
            raise RuntimeError("Could not locate currInitiatives on index page")
        raw = m.group(1)
        # CAISO uses .NET-flavored "/Date(ms)/" strings inline. Replace with
        # null so the JSON parser doesn't choke; we don't need the value
        # since the StartDate also appears in StageA's text.
        cleaned = self._NET_DATE_RE.sub("null", raw)
        data = json.loads(cleaned)

        out = []
        for item in data:
            link_title = (item.get("LinkTitle") or "").strip().rstrip("/")
            # A handful of LinkTitles have a trailing %20; normalize.
            link_title = link_title.replace("%20", "").strip()
            if not link_title:
                continue
            url = self.DETAIL_URL_TEMPLATE.format(slug=link_title)
            # Title is the unphased name; DisplayTitle has "phase N" appended
            # for multi-phase initiatives that share an InitiativeId. Using
            # Title keeps the canonical_name stable across phase rows.
            out.append({
                "native_id":       (item.get("InitiativeId") or "").lower(),
                "canonical_name":  item.get("Title") or item.get("DisplayTitle"),
                "short_title":     item.get("ShortTitle"),
                "url":             url,
                "status":          item.get("Status"),  # Active / Completed / Closed
                "phase":           self._safe_int(item.get("Phase")),
                "eim_categories":  self._clean(item.get("EIMCategories")),
                "stage_a":         self._clean(item.get("StageA")),
                "stage_b":         self._clean(item.get("StageB")),
                "stage_c":         self._clean(item.get("StageC")),
                "stage_d":         self._clean(item.get("StageD")),
                "start_date":      None,  # StartDate is .NET-formatted; skip
            })
        return out

    @staticmethod
    def _safe_int(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clean(v):
        """CAISO sometimes uses the literal string 'None' for absent values."""
        if v is None or v == "None" or (isinstance(v, str) and not v.strip()):
            return None
        return v

    # ── Detail parsing ─────────────────────────────────────────────────────

    _ACTIVITY_RE = re.compile(
        r"getCalendarEvent\(\s*['\"]([0-9a-f-]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*\)",
        flags=re.IGNORECASE,
    )

    def _extract_activities(self, html):
        """
        Return list of (activity_guid, event_id) tuples from the inline
        getCalendarEvent calls on the detail page. event_id may be empty
        when CAISO hasn't yet linked an activity to a calendar entry.
        Deduplicated.
        """
        seen = set()
        out = []
        for m in self._ACTIVITY_RE.finditer(html):
            guid = m.group(1).lower()
            evt_id = m.group(2)
            key = (guid, evt_id)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    def _resolve_event(self, event_id):
        """
        Hit caiso.com/resources/export/event?id=<eid> and return the first
        record (or None on failure / empty response).
        """
        url = self.EVENT_API.format(eid=event_id)
        try:
            txt = self._get(url, json_accept=True)
            data = json.loads(txt)
            if isinstance(data, list) and data:
                return data[0]
            return None
        except Exception:
            return None

    # ── HTTP ───────────────────────────────────────────────────────────────

    def _get(self, url, json_accept=False):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        headers = {"Accept": "application/json"} if json_accept else {}
        resp = self.session.get(url, timeout=60, headers=headers)
        self._last_request_time = time.time()
        resp.raise_for_status()
        return resp.text


if __name__ == "__main__":
    init_db()
    CAISOIssuesScraper().run()
