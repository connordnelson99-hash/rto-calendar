#!/usr/bin/env python3
"""
FERC Press Releases & Headlines Scraper

Scrapes the public news listing at
    https://www.ferc.gov/news-events/news/news-releases-headlines

── Architecture (confirmed via live Playwright browse 2026-04-24) ──

The listing page is behind Cloudflare's managed challenge, so plain
`requests` returns 403. Playwright (already used by the PJM and CAISO
scrapers) passes through fine with a real browser fingerprint.

Each listing item is a `div.views-row` containing:
    .content-feed__label   — "Headlines" or "News Releases"
    .content-feed__title a — permalink to the detail page
    .content-feed__date    — "Month D, YYYY"

Pagination via `?page=N`. Each page shows ~10 items.

Detail pages contain:
    main > article                      — the body text of the release
    Docket No. references (e.g. "RM26-4-000") appear as plain text
    Optional eLibrary link              — `elibrary.ferc.gov/eLibrary/filelist?...`

Modeling decision: each press release becomes its own `meetings` row
(rto="FERC", committee=category). The release itself is stored as a
single `documents` row of type="press-release" whose `extracted_text`
is the body of the release — exactly what Stage 2 screening wants.
"""

import re
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin

from .base_scraper import BaseRTOScraper


class FERCScraper(BaseRTOScraper):

    rto_name = "FERC"

    BASE_URL = "https://www.ferc.gov"
    CALENDAR_URL = "https://www.ferc.gov/news-events/news/news-releases-headlines"

    # Each listing page shows ~10 items. 3 pages = ~30 items, usually
    # enough to cover a month or two of FERC news.
    DEFAULT_LOOKBACK_PAGES = 3

    # Items whose titles match any of these patterns are skipped up front.
    # They're not markets-policy content and would waste Haiku screening budget.
    SKIP_TITLE_PATTERNS = [
        r"\bEnvironmental Impact Statement\b",
        r"\bDraft Environmental\b",
        r"\bNotice of Availability\b",
        r"\bNotice of Schedule\b.*(Pipeline|Expansion Project)",
        r"\bPipeline Project\b",
        r"\bExpansion Project\b",
        r"\bHydroelectric License\b",
        r"\bLicense Application\b",
        r"\bLiquefaction\b",
        r"\bLNG Project\b",
    ]

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, lookback_pages=None):
        super().__init__()
        self.lookback_pages = lookback_pages or self.DEFAULT_LOOKBACK_PAGES

    # ── Orchestration override ────────────────────────────────────────

    def run(self, lookback_days=14, lookahead_days=30, download=True):
        """
        FERC press releases are HTML, not downloadable PDFs. We use a
        single Playwright session to walk the listing and each detail
        page, store metadata, and persist the body text directly to
        `extracted_text`. We skip the binary-download path in the base
        class entirely.
        """
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from db.database import (
            get_connection, upsert_meeting, upsert_document,
            save_extracted_text, save_meeting_screening, log_scrape,
        )

        from playwright.sync_api import sync_playwright

        conn = get_connection()
        start_time = time.time()
        total_items = 0
        total_kept = 0
        total_text_saved = 0
        error = None

        print(f"\n{'='*60}")
        print(f"  FERC Press Releases & Headlines Scraper")
        print(f"{'='*60}")
        print(f"  Walking {self.lookback_pages} listing page(s); "
              f"lookback window = {lookback_days} days")

        # Don't accept items older than this
        cutoff = datetime.now() - timedelta(days=lookback_days * 3)
        # We use 3x lookback for FERC because press releases are a slower
        # cadence than RTO meetings — ~1 per business day, so a 2-month
        # window is more useful than the default 14-day one.

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                ctx = browser.new_context(user_agent=self.USER_AGENT)
                page = ctx.new_page()

                # Phase 1: walk listing pages, collect candidate items
                items = []
                for page_num in range(self.lookback_pages):
                    url = (self.CALENDAR_URL if page_num == 0
                           else f"{self.CALENDAR_URL}?page={page_num}")
                    print(f"\n  -> page {page_num}: {url}")
                    try:
                        page.goto(url, wait_until="networkidle", timeout=45000)
                    except Exception as e:
                        print(f"    page load failed: {e}")
                        continue

                    rows = page.query_selector_all(".views-row")
                    print(f"    {len(rows)} items on page")
                    for row in rows:
                        parsed = self._parse_listing_row(row)
                        if parsed:
                            items.append(parsed)

                total_items = len(items)
                print(f"\n  Found {total_items} listing items total")

                # Phase 2: filter
                kept = []
                for it in items:
                    date_obj = self._parse_date(it["date_text"])
                    if date_obj is None:
                        continue
                    if date_obj < cutoff:
                        continue
                    if self._should_skip(it["title"]):
                        continue
                    it["meeting_date"] = date_obj.strftime("%Y-%m-%d")
                    kept.append(it)

                total_kept = len(kept)
                print(f"  {total_kept} items kept after date + topic filter "
                      f"({total_items - total_kept} skipped)")

                # Phase 3: visit each detail page, capture body text
                for i, it in enumerate(kept, 1):
                    print(f"  [{i}/{total_kept}] {it['category']} | "
                          f"{it['meeting_date']} | {it['title'][:55]}")

                    body_text, docket, extra_links = self._fetch_detail(page, it["detail_url"])
                    if body_text:
                        total_text_saved += 1

                    # Upsert meeting
                    meeting_id = upsert_meeting(
                        conn,
                        rto=self.rto_name,
                        committee=it["category"],
                        title=it["title"],
                        meeting_date=it["meeting_date"],
                        meeting_time=None,
                        location=None,
                        source_url=self.CALENDAR_URL,
                        detail_url=it["detail_url"],
                        materials_url=None,
                    )

                    # Auto-flag every FERC "meeting" as hydro-relevant so
                    # Stage 2 doc screening always runs on the body text.
                    # FERC item titles are often generic ("Sunshine Notice",
                    # "Summaries") — the real signal is inside the article.
                    save_meeting_screening(
                        conn, meeting_id, True,
                        "Auto-flagged: FERC markets-policy item; "
                        "doc-level screener decides per-article relevance.",
                    )

                    # Upsert the press release itself as a document
                    doc_id = upsert_document(
                        conn,
                        meeting_id=meeting_id,
                        rto=self.rto_name,
                        download_url=it["detail_url"],
                        doc_type="press-release",
                        title=it["title"],
                        filename=None,
                        posted_date=it["meeting_date"],
                    )
                    if body_text:
                        text = body_text
                        if docket:
                            text = f"[Docket: {docket}]\n\n{text}"
                        save_extracted_text(conn, doc_id, text)

                    # Capture any linked eLibrary filelists as extra docs.
                    # We don't attempt to download them (eLibrary requires
                    # its own auth flow) — just surface them in the UI.
                    for link_url, link_text in extra_links:
                        upsert_document(
                            conn,
                            meeting_id=meeting_id,
                            rto=self.rto_name,
                            download_url=link_url,
                            doc_type="elibrary",
                            title=link_text or "eLibrary filing",
                            filename=None,
                            posted_date=it["meeting_date"],
                        )

                    # Polite pause between detail fetches
                    time.sleep(0.8)

                browser.close()

        except Exception as e:
            error = str(e)
            print(f"\n  ERROR: {e}")
            raise

        finally:
            duration = time.time() - start_time
            log_scrape(
                conn, self.rto_name, "full_run",
                self.CALENDAR_URL,
                "error" if error else "success",
                total_kept, total_kept, 0,
                error, duration,
            )
            conn.close()
            print(f"\n  Summary: {total_items} seen, {total_kept} stored, "
                  f"{total_text_saved} with body text "
                  f"({duration:.1f}s)")

    # ── These are required by the ABC but unused; see run() override ──

    def scrape_meetings(self, lookback_days=14, lookahead_days=30):
        raise NotImplementedError("FERC uses a custom run() pipeline")

    def scrape_meeting_documents(self, meeting_info):
        raise NotImplementedError("FERC uses a custom run() pipeline")

    # ── Helpers ────────────────────────────────────────────────────────

    def _parse_listing_row(self, row):
        """Extract category, title, url, date from one .views-row element."""
        try:
            label = row.query_selector(".content-feed__label")
            title_a = row.query_selector(".content-feed__title a")
            date_el = row.query_selector(".content-feed__date")

            if not (label and title_a and date_el):
                return None

            category = (label.inner_text() or "").strip()
            title = (title_a.inner_text() or "").strip()
            href = title_a.get_attribute("href") or ""
            date_text = (date_el.inner_text() or "").strip()

            if not (title and href and date_text):
                return None

            return {
                "category": category,
                "title": title,
                "detail_url": urljoin(self.BASE_URL, href),
                "date_text": date_text,
            }
        except Exception:
            return None

    @staticmethod
    def _parse_date(date_text):
        """Parse 'April 16, 2026' → datetime."""
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(date_text, fmt)
            except ValueError:
                continue
        return None

    def _should_skip(self, title):
        """True if the title matches any SKIP_TITLE_PATTERNS."""
        for pat in self.SKIP_TITLE_PATTERNS:
            if re.search(pat, title, re.IGNORECASE):
                return True
        return False

    def _fetch_detail(self, page, url):
        """
        Fetch a detail page, return (body_text, docket_no, extra_links).
            body_text: article body (~few thousand chars typically)
            docket_no: first 'Docket No. XXX' token, or None
            extra_links: list of (url, anchor_text) for real PDF / eLibrary
                filings — site-wide nav chrome ("eLibrary" home link,
                "Download Document" with no target, etc.) is excluded.
        """
        try:
            page.goto(url, wait_until="load", timeout=45000)
            # Wait until the real content has rendered
            page.wait_for_selector("article", timeout=20000)
        except Exception as e:
            print(f"      detail fetch failed: {e}")
            return "", None, []

        # The <article> element on a FERC press-release/headline detail
        # page contains the body copy without breadcrumbs or chrome.
        # We fall back to <main> only if article is missing/empty.
        text = ""
        article = page.query_selector("article")
        if article:
            text = (article.inner_text() or "").strip()
        if len(text) < 200:
            main = page.query_selector("main")
            if main:
                text = (main.inner_text() or "").strip()

        # Extract docket number if present
        docket = None
        m = re.search(r"Docket No\.\s*([A-Z0-9-]+)", text)
        if m:
            docket = m.group(1)

        # Pull linked PDFs / eLibrary filings. FERC detail pages
        # frequently include an "attached document" link with the
        # anchor text "Download Document / Descargar Documento"; we
        # want those, but not the site-wide nav link to eLibrary home.
        extra_links = []
        seen = set()
        try:
            links = page.eval_on_selector_all(
                "main a[href]",
                "els => els.map(a => ({text: (a.innerText||'').trim().slice(0,200), href: a.href}))",
            )
            for link in links:
                href = (link.get("href") or "").strip()
                anchor = (link.get("text") or "").strip()
                if not href or href in seen:
                    continue

                # Keep only real document links
                is_pdf = href.lower().endswith(".pdf")
                is_elibrary_filing = (
                    "elibrary.ferc.gov/eLibrary/filelist" in href
                    or "elibrary.ferc.gov/eLibrary/FileList" in href
                )
                is_site_file = "/sites/default/files/" in href
                if not (is_pdf or is_elibrary_filing or is_site_file):
                    continue

                # Derive a friendlier title when the anchor is generic
                title = anchor
                low_anchor = anchor.lower()
                generic = (
                    low_anchor == ""
                    or "download document" in low_anchor
                    or low_anchor in {"elibrary", "descargar documento"}
                )
                if generic:
                    # Use the filename from the URL
                    try:
                        from urllib.parse import unquote, urlparse
                        tail = unquote(urlparse(href).path.rsplit("/", 1)[-1])
                        title = tail or "Attached document"
                    except Exception:
                        title = "Attached document"

                seen.add(href)
                extra_links.append((href, title))
        except Exception:
            pass

        return text, docket, extra_links
