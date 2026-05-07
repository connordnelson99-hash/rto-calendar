#!/usr/bin/env python3
"""
ISO-NE Key Projects scraper.

Unlike PJM (HTML class flags) and CAISO (inlined JS array), ISO-NE exposes
a Solr-backed search API for content tagged against a "Key Issue" — that's
the closest equivalent of an initiative on their side.

Endpoints:
    docs:    /api/1/services/documents.json
             ?pre_key_issue_value=<name>&start=N&rows=M&sort=publish_date_dt+desc
    facets:  same endpoint with rows=0&facets=key_issue_value to enumerate
             every key issue in the corpus.
    events:  /calendar?eventId=<key>  (we just construct this — no API hit
             needed, the URL format already matches what we store in
             meetings.detail_url for ISO-NE.)

Each document record gives us BOTH a doc URL (path field) AND a list of
linked event IDs (events_o), so a single ref pass populates document- and
meeting-level joins simultaneously.
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
)


class ISONEIssuesScraper:

    BASE_URL = "https://www.iso-ne.com"
    INDEX_URL = "https://www.iso-ne.com/committees/key-projects/"
    DOCS_API = "https://www.iso-ne.com/api/1/services/documents.json"
    EVENT_URL_TEMPLATE = "https://www.iso-ne.com/calendar?eventId={key}"
    PROJECT_URL_TEMPLATE = "https://www.iso-ne.com/committees/key-projects/{slug}"

    PAGE_SIZE = 50          # docs per API page
    REQUEST_DELAY = 0.4     # polite delay between API calls

    rto_name = "ISO-NE"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        })
        self._last_request_time = 0.0

    # ── Public entrypoint ───────────────────────────────────────────────────

    def run(self, refresh_closed=False):
        """
        Discover key projects + populate document/meeting cross-references.

        refresh_closed=False (default) skips the corpus pull for projects
        flagged Implemented on the index page. They still get index-row
        metadata stored. Pass True to refresh historical projects too.
        """
        conn = get_connection()
        start = time.time()

        print(f"\n{'='*60}")
        print(f"  ISO-NE Key Projects Scraper")
        print(f"{'='*60}")

        try:
            # Two complementary sources:
            #   - The Key Projects landing page tells us which projects are
            #     currently Active (i.e. on the public list) and gives us
            #     friendly URL slugs.
            #   - The docs API facets tell us every key_issue value that
            #     has at least one document tagged against it — including
            #     historical projects that have rolled off the landing page.
            active_index = self._scrape_index()
            active_names = {p["canonical_name"].lower() for p in active_index}
            print(f"  Index: {len(active_index)} active key projects")

            facets = self._fetch_key_issue_facet()
            print(f"  Facet: {len(facets)} unique key issues in doc corpus")

            # Build a unified initiative list: every facet entry, plus any
            # active project that didn't show up in the facets (rare, but
            # means it's a brand-new project with no docs yet).
            seen = set()
            initiatives = []
            for name, doc_count in facets:
                key = name.lower()
                seen.add(key)
                idx = next((p for p in active_index if p["canonical_name"].lower() == key), None)
                initiatives.append({
                    "canonical_name": name,
                    "doc_count": doc_count,
                    "url": idx["url"] if idx else None,
                    "status": "Active" if key in active_names else "Implemented",
                    "native_id": (idx["slug"] if idx else self._slugify(name)),
                })
            for p in active_index:
                if p["canonical_name"].lower() not in seen:
                    initiatives.append({
                        "canonical_name": p["canonical_name"],
                        "doc_count": 0,
                        "url": p["url"],
                        "status": "Active",
                        "native_id": p["slug"],
                    })

            # Upsert metadata for every initiative.
            for stub in initiatives:
                upsert_issue(
                    conn, self.rto_name, stub["native_id"],
                    url=stub["url"],
                    canonical_name=stub["canonical_name"],
                    status=stub["status"],
                    is_open=1 if stub["status"] == "Active" else 0,
                )

            # Pull docs only for active projects by default.
            targets = [
                i for i in initiatives
                if refresh_closed or i["status"] == "Active"
            ]
            ref_count = 0
            for i, stub in enumerate(targets, 1):
                name_short = stub["canonical_name"][:55]
                print(f"  [{i}/{len(targets)}] {name_short}", end=" ... ", flush=True)
                try:
                    issue_id = conn.execute(
                        "SELECT id FROM issues WHERE rto=? AND native_id=?",
                        (self.rto_name, stub["native_id"]),
                    ).fetchone()["id"]

                    docs_added = 0
                    events_added = 0
                    seen_event_urls = set()
                    seen_doc_urls = set()

                    for doc in self._iter_docs(stub["canonical_name"]):
                        # Doc URL → matches documents.download_url
                        path = doc.get("path") or ""
                        if path:
                            doc_url = urljoin(self.BASE_URL, path)
                            if doc_url not in seen_doc_urls:
                                seen_doc_urls.add(doc_url)
                                title = doc.get("document_title_s") or None
                                upsert_issue_reference(conn, issue_id, doc_url, title)
                                docs_added += 1

                        # Each linked event → matches meetings.detail_url.
                        # Crafter wraps a single record as `{item: {...}}` and
                        # multiple records as `{item: [{...}, {...}]}`, so
                        # flatten before reading key/value_smv.
                        for item in self._flatten_items(doc.get("events_o")):
                            ev_key = item.get("key")
                            if not ev_key:
                                continue
                            ev_url = self.EVENT_URL_TEMPLATE.format(key=ev_key)
                            if ev_url in seen_event_urls:
                                continue
                            seen_event_urls.add(ev_url)
                            ev_title = item.get("value_smv") or None
                            upsert_issue_reference(conn, issue_id, ev_url, ev_title)
                            events_added += 1

                    ref_count += docs_added + events_added
                    print(f"{docs_added} docs · {events_added} events")
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

    # ── Index page (gives us active set + slug URLs) ───────────────────────

    _PROJECT_LINK_RE = re.compile(r"^/committees/key-projects/([^/?#]+)/?$")

    def _scrape_index(self):
        """
        Parse the Key Projects landing page for the active set. Each project
        is rendered as <a href="/committees/key-projects/{slug}">{title}</a>.
        Skip the "Implemented" link (which goes to the historical archive).
        """
        html = self._get(self.INDEX_URL)
        soup = BeautifulSoup(html, "html.parser")
        seen = set()
        projects = []
        for a in soup.find_all("a", href=self._PROJECT_LINK_RE):
            href = a.get("href", "")
            slug = self._PROJECT_LINK_RE.match(href).group(1)
            if slug in ("implemented", ""):
                continue
            if slug in seen:
                continue
            seen.add(slug)
            title = a.get_text(strip=True)
            # Strip the " Key Project" suffix the page appends to link text.
            canonical = re.sub(r"\s+Key Project\s*$", "", title).strip()
            if not canonical:
                continue
            projects.append({
                "slug": slug,
                "canonical_name": canonical,
                "url": self.PROJECT_URL_TEMPLATE.format(slug=slug),
            })
        return projects

    @staticmethod
    def _flatten_items(field):
        """
        Yield {key, value_smv} dicts from a Crafter `*_o` field. The wrapper
        is always a list; each entry is `{"item": <single dict OR list of
        dicts>}`. Both shapes flatten to a single iterable of records.
        """
        for entry in (field or []):
            if not isinstance(entry, dict):
                continue
            inner = entry.get("item")
            if isinstance(inner, dict):
                yield inner
            elif isinstance(inner, list):
                for sub in inner:
                    if isinstance(sub, dict):
                        yield sub

    @staticmethod
    def _slugify(name):
        s = re.sub(r"[^\w\s-]", "", name.lower())
        s = re.sub(r"\s+", "-", s).strip("-")
        return s[:60] or "unknown"

    # ── Docs API ───────────────────────────────────────────────────────────

    def _fetch_key_issue_facet(self):
        """
        Single facet-only call to enumerate every distinct key_issue_value
        in the doc corpus. Returns [(name, count), ...] sorted by count desc.
        """
        params = {
            "type": ["doc", "ceii"],
            "crafterSite": "iso-ne",
            "searchable": "true",
            "includeVersions": "false",
            "q": "*",
            "source": "docLibraryWidget",
            "start": 0,
            "rows": 0,
            "facets": "key_issue_value",
            "key_issue_value.sort": "count",
        }
        data = self._get_json(self.DOCS_API, params=params)
        facets = data.get("facets", {})
        # Crafter returns the facet as a {name: count} dict.
        raw = facets.get("key_issue_value") or {}
        out = []
        if isinstance(raw, dict):
            for name, count in raw.items():
                if isinstance(name, str) and name.strip():
                    out.append((name.strip(), int(count)))
        return sorted(out, key=lambda x: -x[1])

    def _iter_docs(self, key_issue_value):
        """Generator yielding every document tagged against a key issue."""
        start = 0
        while True:
            params = {
                "type": ["doc", "ceii"],
                "crafterSite": "iso-ne",
                "searchable": "true",
                "includeVersions": "false",
                "q": "*",
                "source": "docLibraryWidget",
                "pre_key_issue_value": key_issue_value,
                "start": start,
                "rows": self.PAGE_SIZE,
                "sort": "publish_date_dt desc",
            }
            data = self._get_json(self.DOCS_API, params=params)
            docs = data.get("documents") or []
            if not docs:
                break
            for d in docs:
                yield d
            total = int(data.get("total") or 0)
            start += len(docs)
            if start >= total:
                break

    # ── HTTP ───────────────────────────────────────────────────────────────

    def _get(self, url, params=None):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        resp = self.session.get(url, params=params, timeout=60)
        self._last_request_time = time.time()
        resp.raise_for_status()
        return resp.text

    def _get_json(self, url, params=None):
        return json.loads(self._get(url, params=params))


if __name__ == "__main__":
    init_db()
    ISONEIssuesScraper().run()
