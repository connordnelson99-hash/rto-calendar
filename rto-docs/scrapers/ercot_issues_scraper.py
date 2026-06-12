#!/usr/bin/env python3
"""
ERCOT Revision Request (initiative) scraper.

ERCOT is the rare RTO with a genuinely structured revision-request
tracker. Every revision request (NPRR, NOGRR, PGRR, ...) has a uniform
issue page at /mktrules/issues/<ID> with four data tables:

    Summary    — Title, Next Group (committee acronym), Next Step, Status
    Action     — dated governance history; each row's date links to the
                 EXACT /calendar/<slug> meeting URL we already store in
                 meetings.detail_url, so issue→meeting references resolve
                 directly (the CAISO pattern, but cleaner).
    Background — Status, Date Posted, Sponsor, Sections, Description
    Key Documents — the official filing trail (direct /files/docs/ links)

Discovery: each revision-request type has a list page at
/mktrules/issues/<type> with a "Pending" table (Issue, Title, Next
Group) and — for NPRRs at least — a "Recently approved within the last
30 days" table (Issue, Title, Approved On). Historical archives exist
under /mktrules/issues/reports/<type> but are deliberately not scraped:
the calendar only needs open work plus fresh completions, matching the
ISO-NE active-projects approach.

Timeline rendering: ERCOT publishes no target dates, so the PJM-style
initiated→target bar doesn't fit. Instead the Action history (plus Date
Posted) is serialized into `stage_a` in CAISO's "Month D, YYYY label"
line format, and webcal-v2 routes ERCOT through the same CaisoTimeline
milestone bar — every governance action becomes a dot. `stage_d` is set
to "Completed" on terminal statuses (Approved/Rejected/Withdrawn) which
is what CaisoTimeline keys on for the completed state.

Sponsor (e.g. "Joint Sponsors", "Lower Colorado River Authority") is
stored in `facilitator` and surfaced via committee_owner_label, which
InitiativeCard already renders as the card's meta line.
"""

import re
import sys
import time
from datetime import datetime
from html import unescape
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import (
    get_connection, init_db,
    upsert_issue, upsert_issue_reference,
    resolve_issue_references, log_scrape,
)

# Pending table rows: <td><a href="/mktrules/issues/NPRR1214">NPRR1214</a></td>
#                     <td>Title...</td>
#                     <td><a href="/widgets/committee?find=prs">PRS</a></td>
_LIST_ROW_RE = re.compile(
    r'<tr\s+class="rrtr[^"]*"\s*>\s*'
    r'<td><a href="/mktrules/issues/([A-Z]+\d+)">[^<]*</a></td>\s*'
    r"<td>(.*?)</td>\s*"
    r"<td>(.*?)</td>",
    re.S)
_H4_RE = re.compile(r"<h4\s*>([^<]+)</h4>")
_ROWHEADER_RE = re.compile(
    r"<tr>\s*<th>([^<]+?):?\s*</th>\s*<td>(.*?)</td>\s*</tr>", re.S)
_ACTION_ROW_RE = re.compile(
    r'<td class="date">\s*(?:<a href="([^"]*)">)?\s*([\d/]+)\s*(?:</a>)?\s*</td>\s*'
    r'<td class="committee">\s*(.*?)\s*</td>\s*'
    r'<td class="rr-action">\s*(.*?)\s*</td>',
    re.S)
_KEY_DOC_RE = re.compile(r'<a\s+download\s+href="([^"]+)"\s+title="([^"]*)"')
_TAG_RE = re.compile(r"<[^>]+>")

# Status can embed a date ("Approved on 05/29/2026"), so match on prefix.
TERMINAL_PREFIXES = ("approved", "rejected", "withdrawn", "implemented")


def _strip(html_fragment):
    return unescape(_TAG_RE.sub("", html_fragment or "")).strip()


def _iso_date(text, fmts=("%m/%d/%Y", "%b %d, %Y", "%B %d, %Y")):
    text = (text or "").strip()
    for fmt in fmts:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _long_date(iso):
    """'2026-06-10' -> 'June 10, 2026' (CaisoTimeline's line format)."""
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d.strftime('%B')} {d.day}, {d.year}"


class ERCOTIssuesScraper:

    rto_name = "ERCOT"

    BASE_URL = "https://www.ercot.com"
    LIST_URL_TEMPLATE = "https://www.ercot.com/mktrules/issues/{rr_type}"
    ISSUE_URL_TEMPLATE = "https://www.ercot.com/mktrules/issues/{native_id}"

    # Every revision-request type with a public list page (verified live
    # 2026-06-12). All are kept — only ~80 open issues total, and hydro
    # relevance is decided downstream by doc screening, not here.
    RR_TYPES = ["nprr", "nogrr", "pgrr", "smogrr", "vcmrr",
                "rmgrr", "copmgrr", "lpgrr", "rrgrr", "obdrr"]

    REQUEST_DELAY = 1.0

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        })
        self._last_request_time = 0.0

    # ── Public entrypoint ────────────────────────────────────────

    def run(self, refresh_closed=False):
        """
        Discover open + recently-approved revision requests, fetch each
        issue page for status/history/sponsor, and populate references.

        refresh_closed is accepted for registry-call compatibility but is
        a no-op: ERCOT's list pages only carry current work (historical
        archives live in separate report pages we deliberately skip).
        """
        conn = get_connection()
        start = time.time()

        print(f"\n{'='*60}")
        print(f"  ERCOT Revision Requests Scraper")
        print(f"{'='*60}")

        try:
            stubs = []
            for rr_type in self.RR_TYPES:
                rows = self._scrape_list(rr_type)
                stubs.extend(rows)
                if rows:
                    print(f"  {rr_type.upper():8} {len(rows)} listed")
            print(f"  Total: {len(stubs)} revision requests")

            ref_count = 0
            for i, stub in enumerate(stubs, 1):
                native_id = stub["native_id"]
                print(f"  [{i}/{len(stubs)}] {native_id}", end=" ... ", flush=True)
                try:
                    detail = self._scrape_issue(native_id)
                    merged = {**stub, **{k: v for k, v in detail.items()
                                         if v is not None}}
                    status = (merged.get("status") or "").strip()
                    terminal = status.lower().startswith(TERMINAL_PREFIXES)
                    is_open = 0 if terminal else 1

                    label_bits = []
                    if merged.get("committee_owner"):
                        label_bits.append(f"Next: {merged['committee_owner']}")
                    if merged.get("sponsor"):
                        label_bits.append(f"Sponsor: {merged['sponsor']}")

                    issue_id = upsert_issue(
                        conn, self.rto_name, native_id,
                        url=self.ISSUE_URL_TEMPLATE.format(native_id=native_id),
                        canonical_name=(f"{native_id} — {merged['title']}"
                                        if merged.get("title") else native_id),
                        status=status or None,
                        stakeholder_phase=merged.get("next_step"),
                        committee_owner=merged.get("committee_owner"),
                        committee_owner_label=" · ".join(label_bits) or None,
                        is_open=is_open,
                        initiated_date=merged.get("initiated_date"),
                        actual_completion_date=(
                            merged.get("completion_date") if terminal else None),
                        facilitator=merged.get("sponsor"),
                        stage_a=merged.get("stage_a"),
                        stage_d="Completed" if terminal else None,
                    )

                    refs = 0
                    for ref_url, ref_title in merged.get("references", []):
                        upsert_issue_reference(conn, issue_id, ref_url, ref_title)
                        refs += 1
                    ref_count += refs
                    print(f"{status or '?'} · {refs} refs")
                except Exception as e:
                    print(f"ERROR: {e}")

            stats = resolve_issue_references(conn)
            print(f"\n  References: {stats['doc_matched']} doc-matched, "
                  f"{stats['meeting_matched']} meeting-matched, "
                  f"{stats['unmatched']} unmatched (external)")

            duration = time.time() - start
            log_scrape(conn, self.rto_name, "issues",
                       self.LIST_URL_TEMPLATE.format(rr_type="nprr"),
                       "success", events_found=len(stubs),
                       docs_found=ref_count, duration_seconds=duration)
            print(f"  Done in {duration:.1f}s")

        except Exception as e:
            duration = time.time() - start
            log_scrape(conn, self.rto_name, "issues",
                       self.LIST_URL_TEMPLATE.format(rr_type="nprr"),
                       "error", error_message=str(e),
                       duration_seconds=duration)
            print(f"  ERROR: {e}")
            raise
        finally:
            conn.close()

    # ── List pages ───────────────────────────────────────────────

    def _scrape_list(self, rr_type):
        """One list page → stub dicts. The page holds a Pending table and
        sometimes a 'Recently approved within the last 30 days' table; the
        third column disambiguates them (committee link vs MM/DD/YYYY)."""
        html = self._get(self.LIST_URL_TEMPLATE.format(rr_type=rr_type))
        stubs, seen = [], set()
        for m in _LIST_ROW_RE.finditer(html):
            native_id, title_html, third_html = m.groups()
            if native_id in seen:
                continue
            seen.add(native_id)
            third = _strip(third_html)
            stub = {
                "native_id": native_id,
                "title": _strip(title_html) or None,
                "committee_owner": None,
                "completion_date": None,
            }
            approved_on = _iso_date(third)
            if approved_on:
                stub["completion_date"] = approved_on
            elif third:
                stub["committee_owner"] = third
            stubs.append(stub)
        return stubs

    # ── Issue pages ──────────────────────────────────────────────

    def _scrape_issue(self, native_id):
        html = self._get(self.ISSUE_URL_TEMPLATE.format(native_id=native_id))

        # Summary + Background share the <th>Label</th><td>value</td> shape;
        # collect them all (later duplicates like Status agree anyway).
        fields = {}
        for label, value_html in _ROWHEADER_RE.findall(html):
            key = label.strip().lower()
            if key not in fields:
                fields[key] = _strip(value_html)

        detail = {
            "title": fields.get("title") or None,
            "status": fields.get("status") or None,
            "next_step": fields.get("next step") or None,
            "committee_owner": fields.get("next group") or None,
            "sponsor": fields.get("sponsor") or None,
            "initiated_date": _iso_date(fields.get("date posted")),
        }

        # Action history → milestone lines + meeting references.
        milestones = []   # (iso_date, label)
        references = []   # (url, title)
        seen_refs = set()
        if detail["initiated_date"]:
            milestones.append((detail["initiated_date"], "Posted"))
        for url, date_text, body_html, action_html in _ACTION_ROW_RE.findall(html):
            iso = _iso_date(date_text)
            body = _strip(body_html)
            action = _strip(action_html)
            if iso and action:
                milestones.append(
                    (iso, f"{body}: {action}" if body else action))
            if url and url not in seen_refs:
                seen_refs.add(url)
                references.append((url, f"{native_id}: {action or 'action'}"))

        # Key Documents (official filing trail).
        for url, title in _KEY_DOC_RE.findall(html):
            if url not in seen_refs:
                seen_refs.add(url)
                references.append((url, unescape(title).strip() or None))

        milestones.sort()
        detail["stage_a"] = "\n".join(
            f"{_long_date(iso)} {label}" for iso, label in milestones) or None
        detail["references"] = references

        # Completion date: prefer the date embedded in the status
        # ("Approved on 05/29/2026"); fall back to the latest action date.
        m = re.search(r"(?i)\bon\s+([\d/]+)", detail["status"] or "")
        embedded = _iso_date(m.group(1)) if m else None
        if embedded:
            detail["completion_date"] = embedded
        elif milestones:
            detail["completion_date"] = milestones[-1][0]
        return detail

    # ── HTTP ─────────────────────────────────────────────────────

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
    ERCOTIssuesScraper().run()
