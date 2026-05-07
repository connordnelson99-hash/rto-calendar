#!/usr/bin/env python3
"""
PJM Issue Tracking scraper.

Two-pass:
  1. Fetch the index page (~14 MB, plain HTML) and harvest one record per issue
     row. Status, stakeholder phase, committee owner, open/closed and the annual
     plan year are all encoded in CSS class flags on `<tr class="item-row ...">`,
     so we don't parse rendered cells. The row's `data-id` is the GUID we key on.

  2. For each active (open, non-canceled) issue, fetch the detail page and
     extract:
       - timeline dates (initiated / work begins / target / actual completion)
       - facilitator and SME
       - all `/-/media/...` document URLs cited on the page

Document URLs cited on the issue page are byte-for-byte the same URLs the
existing PJM scraper stores in `documents.download_url` (just relative on the
issue page). Cross-reference is a deterministic URL string match.

URL shape:
  Index:    /committees-and-groups/issue-tracking.aspx
  Detail:   /committees-and-groups/issue-tracking/issue-tracking-details.aspx?Issue={guid}
  Detail (non-stakeholder, 6 of ~440):
            /committees-and-groups/issue-tracking/issue-tracking-details-non-stakeholder.aspx?Issue={guid}
"""

import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import (
    get_connection, init_db,
    upsert_issue, upsert_issue_reference,
    resolve_issue_references, log_scrape,
)


class PJMIssuesScraper:

    BASE_URL = "https://www.pjm.com"
    INDEX_URL = "https://www.pjm.com/committees-and-groups/issue-tracking.aspx"
    REQUEST_DELAY = 1.5

    rto_name = "PJM"

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
        Discover issues, populate references for active ones, resolve URL
        matches against the existing documents table.

        refresh_closed=False (default) skips detail-page fetches for closed
        and canceled issues. They still get their index-row metadata stored.
        """
        conn = get_connection()
        start = time.time()

        print(f"\n{'='*60}")
        print(f"  PJM Issue Tracking Scraper")
        print(f"{'='*60}")

        try:
            html = self._get(self.INDEX_URL)
            issues = list(self._parse_index(html))
            print(f"  Index: {len(issues)} issues")

            active = [i for i in issues
                      if i["is_open"] and i["status"] not in ("closed", "canceled")]
            print(f"  Active (will fetch details): {len(active)}")

            for stub in issues:
                upsert_issue(
                    conn, self.rto_name, stub["native_id"],
                    url=stub["url"],
                    canonical_name=stub["canonical_name"],
                    status=stub["status"],
                    stakeholder_phase=stub["stakeholder_phase"],
                    committee_owner=stub["committee_owner"],
                    committee_owner_label=stub["committee_owner_label"],
                    is_open=1 if stub["is_open"] else 0,
                    annual_plan_year=stub["annual_plan_year"],
                )

            targets = issues if refresh_closed else active
            ref_count = 0
            for i, stub in enumerate(targets, 1):
                name = (stub["canonical_name"] or "")[:55]
                print(f"  [{i}/{len(targets)}] {name}", end=" ... ", flush=True)
                try:
                    detail_html = self._get(stub["url"])
                    detail = self._parse_detail(detail_html)

                    upsert_issue(
                        conn, self.rto_name, stub["native_id"],
                        initiated_date=detail["initiated_date"],
                        work_begins_date=detail["work_begins_date"],
                        target_completion_date=detail["target_completion_date"],
                        actual_completion_date=detail["actual_completion_date"],
                        facilitator=detail["facilitator"],
                        sme=detail["sme"],
                    )

                    issue_id = conn.execute(
                        "SELECT id FROM issues WHERE rto=? AND native_id=?",
                        (self.rto_name, stub["native_id"]),
                    ).fetchone()["id"]

                    for url, title in detail["references"].items():
                        upsert_issue_reference(conn, issue_id, url, title)
                        ref_count += 1
                    print(f"{len(detail['references'])} refs")
                except Exception as e:
                    print(f"ERROR: {e}")

            stats = resolve_issue_references(conn)
            print(f"\n  References: {stats['doc_matched']} doc-matched, "
                  f"{stats['meeting_matched']} meeting-matched, "
                  f"{stats['unmatched']} unmatched (external)")

            duration = time.time() - start
            log_scrape(
                conn, self.rto_name, "issues",
                self.INDEX_URL, "success",
                events_found=len(issues),
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

    _CLASS_PREFIXES = {
        "optisssts-":      "status",
        "optstkhldprst-":  "stakeholder_phase",
        "optcmt-":         "committee_owner",
    }

    def _parse_index(self, html):
        """Yield one stub dict per `<tr class="item-row">` on the index page."""
        soup = BeautifulSoup(html, "html.parser")
        for row in soup.find_all("tr", class_="item-row"):
            classes = row.get("class", [])
            stub = {
                "native_id": (row.get("data-id") or "").lower(),
                "status": None,
                "stakeholder_phase": None,
                "committee_owner": None,
                "committee_owner_label": None,
                "is_open": None,
                "annual_plan_year": None,
                "canonical_name": None,
                "url": None,
            }
            for c in classes:
                for prefix, field in self._CLASS_PREFIXES.items():
                    if c.startswith(prefix):
                        # `~` in class names stands in for spaces
                        stub[field] = c[len(prefix):].replace("~", " ")
                if c.startswith("selopncls-"):
                    stub["is_open"] = (c[len("selopncls-"):] == "open")
                elif c.startswith("selannualyear-"):
                    try:
                        stub["annual_plan_year"] = int(c[len("selannualyear-"):])
                    except ValueError:
                        pass

            name_link = row.select_one("td.issue-name-col a")
            if name_link is not None:
                stub["canonical_name"] = name_link.get_text(strip=True)
                href = name_link.get("href") or ""
                stub["url"] = urljoin(self.BASE_URL, href) if href else None

            owner_link = row.select_one("td.issue-owner-col a")
            if owner_link is not None:
                stub["committee_owner_label"] = (
                    owner_link.get("title") or owner_link.get_text(strip=True) or None
                )

            if stub["native_id"] and stub["url"]:
                yield stub

    # ── Detail parsing ──────────────────────────────────────────────────────

    _TIMELINE_CLASSES = {
        "issue-initiated":    "initiated_date",
        "work-begins":        "work_begins_date",
        "target-completion":  "target_completion_date",
        "actual-completion":  "actual_completion_date",
    }

    _DETAIL_ROW_TO_FIELD = {
        "trFacilitator": "facilitator",
        "trSME":         "sme",
    }

    def _parse_detail(self, html):
        """Extract timeline dates, facilitator/SME, and all /-/media/ doc URLs."""
        soup = BeautifulSoup(html, "html.parser")

        out = {
            "initiated_date": None,
            "work_begins_date": None,
            "target_completion_date": None,
            "actual_completion_date": None,
            "facilitator": None,
            "sme": None,
            "references": {},  # absolute_url -> ref_title (or None)
        }

        # Timeline boxes
        for box in soup.find_all("div", class_="issue-box-base"):
            classes = box.get("class", [])
            for css_cls, field in self._TIMELINE_CLASSES.items():
                if css_cls in classes:
                    text = box.get_text(strip=True)
                    # "Issue initiated: 3.10.2026" → take everything after the colon
                    _, _, val = text.partition(":")
                    out[field] = self._normalize_date(val.strip())

        # Issue Details rows — keyed by their <tr id="...">
        for row_id, field in self._DETAIL_ROW_TO_FIELD.items():
            tr = soup.find("tr", id=re.compile(rf"{row_id}$"))
            if tr is None:
                continue
            tds = tr.find_all("td")
            if len(tds) >= 2:
                val = tds[1].get_text(" ", strip=True)
                if val:
                    out[field] = val

        # All document links cited on the page
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "/-/media/" not in href:
                continue
            abs_url = urljoin(self.BASE_URL, href)
            if abs_url in out["references"]:
                continue
            # First direct-text child gives the label ("Agenda", "Problem Statement")
            label = None
            for child in a.contents:
                if isinstance(child, str):
                    s = child.strip()
                    if s:
                        label = s
                        break
            out["references"][abs_url] = label

        return out

    @staticmethod
    def _normalize_date(s):
        """PJM uses M.D.YYYY. 'TBD' / blank → None. Returns YYYY-MM-DD."""
        if not s:
            return None
        s = s.strip()
        if not s or s.upper() == "TBD":
            return None
        m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", s)
        if not m:
            return None
        mm, dd, yyyy = m.groups()
        try:
            return datetime(int(yyyy), int(mm), int(dd)).strftime("%Y-%m-%d")
        except ValueError:
            return None

    # ── HTTP ───────────────────────────────────────────────────────────────

    def _get(self, url):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        resp = self.session.get(url, timeout=60)
        self._last_request_time = time.time()
        resp.raise_for_status()
        return resp.text


if __name__ == "__main__":
    init_db()
    PJMIssuesScraper().run()
