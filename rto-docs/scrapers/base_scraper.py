#!/usr/bin/env python3
"""
Abstract base class for RTO document scrapers.

Each RTO scraper inherits from this and implements:
    - rto_name (property)
    - scrape_meetings(lookback_days, lookahead_days)
    - scrape_meeting_documents(meeting_info)
"""

import os
import re
import time
import hashlib
import requests
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, unquote

try:
    import pdfplumber
    _PDFPLUMBER_AVAILABLE = True
except ImportError:
    _PDFPLUMBER_AVAILABLE = False


class BaseRTOScraper(ABC):

    DOCS_DIR = Path(__file__).parent.parent / "docs"
    REQUEST_DELAY = 1.5  # seconds between requests

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
        self._last_request_time = 0

    @property
    @abstractmethod
    def rto_name(self):
        """Return the RTO identifier string (e.g. 'PJM', 'CAISO')."""
        ...

    @abstractmethod
    def scrape_meetings(self, lookback_days=14, lookahead_days=30):
        """
        Discover meetings from the RTO calendar.

        Returns a list of dicts with at least:
            - title: str
            - meeting_date: str (YYYY-MM-DD)
            - meeting_time: str (optional)
            - committee: str (optional)
            - source_url: str (calendar URL)
            - detail_url: str (meeting detail page URL, optional)
            - materials_url: str (GUID-based materials page, optional)
        """
        ...

    @abstractmethod
    def scrape_meeting_documents(self, meeting_info):
        """
        Find documents for a specific meeting.

        Args:
            meeting_info: dict from scrape_meetings()

        Returns a list of dicts with:
            - download_url: str
            - doc_type: str ('agenda', 'minutes', 'presentation', 'other')
            - title: str (optional)
            - filename: str (optional)
            - posted_date: str (optional)
        """
        ...

    def run(self, lookback_days=14, lookahead_days=30, download=True):
        """
        Full scrape pipeline: discover meetings, find documents,
        optionally download them.
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from db.database import (
            get_connection, upsert_meeting, upsert_document,
            mark_downloaded, save_extracted_text, log_scrape
        )

        conn = get_connection()
        start_time = time.time()
        total_events = 0
        total_docs = 0
        total_downloaded = 0

        print(f"\n{'='*60}")
        print(f"  {self.rto_name} Document Scraper")
        print(f"{'='*60}")

        try:
            # Phase 1: Discover meetings
            meetings = self.scrape_meetings(lookback_days, lookahead_days)
            total_events = len(meetings)
            print(f"\n  Found {total_events} meetings")

            for meeting in meetings:
                meeting_id = upsert_meeting(
                    conn,
                    rto=self.rto_name,
                    committee=meeting.get("committee"),
                    title=meeting["title"],
                    meeting_date=meeting["meeting_date"],
                    meeting_time=meeting.get("meeting_time"),
                    location=meeting.get("location"),
                    source_url=meeting.get("source_url"),
                    detail_url=meeting.get("detail_url"),
                    materials_url=meeting.get("materials_url"),
                )

                # Phase 2: Find documents for each meeting
                documents = self.scrape_meeting_documents(meeting)
                total_docs += len(documents)

                if documents:
                    print(f"    {meeting['title'][:50]}... -> {len(documents)} docs")

                for doc in documents:
                    doc_id = upsert_document(
                        conn,
                        meeting_id=meeting_id,
                        rto=self.rto_name,
                        download_url=doc["download_url"],
                        doc_type=doc.get("doc_type"),
                        title=doc.get("title"),
                        filename=doc.get("filename"),
                        posted_date=doc.get("posted_date"),
                    )

                    # Phase 3: Download + extract text
                    if download and doc.get("download_url"):
                        local_path, extracted_text = self._download_document(
                            doc["download_url"],
                            meeting.get("committee", "general"),
                            meeting["meeting_date"],
                        )
                        if local_path:
                            file_size = os.path.getsize(local_path)
                            mark_downloaded(conn, doc_id, str(local_path),
                                            file_size=file_size)
                            total_downloaded += 1
                            if extracted_text:
                                save_extracted_text(conn, doc_id, extracted_text)

            duration = time.time() - start_time
            log_scrape(conn, self.rto_name, "full_run",
                       getattr(self, "CALENDAR_URL", ""),
                       "success", total_events, total_docs,
                       total_downloaded, duration_seconds=duration)

            print(f"\n  Summary: {total_events} meetings, "
                  f"{total_docs} docs found, "
                  f"{total_downloaded} downloaded "
                  f"({duration:.1f}s)")

        except Exception as e:
            duration = time.time() - start_time
            log_scrape(conn, self.rto_name, "full_run",
                       getattr(self, "CALENDAR_URL", ""),
                       "error", total_events, total_docs,
                       total_downloaded, str(e), duration)
            print(f"\n  ERROR: {e}")
            raise

        finally:
            conn.close()

    # ── Helper methods ──────────────────────────────────────────

    def _polite_delay(self):
        """Rate limit requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        self._last_request_time = time.time()

    def _date_range(self, lookback_days, lookahead_days):
        """Return (start_date, end_date) as YYYY-MM-DD strings."""
        today = datetime.now()
        start = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end = (today + timedelta(days=lookahead_days)).strftime("%Y-%m-%d")
        return start, end

    def _classify_doc(self, filename_or_title):
        """Basic document type classification from filename/title."""
        text = (filename_or_title or "").lower()
        if re.search(r"agenda", text):
            return "agenda"
        elif re.search(r"minutes|draft-minutes", text):
            return "minutes"
        elif re.search(r"presentation|slide|briefing", text):
            return "presentation"
        elif re.search(r"report|summary|update", text):
            return "report"
        elif re.search(r"vote|poll|ballot|motion", text):
            return "vote"
        elif re.search(r"manual|m\d{2}", text):
            return "manual"
        elif re.search(r"matrix", text):
            return "matrix"
        elif re.search(r"issue.?charge|problem.?statement", text):
            return "issue-charge"
        elif re.search(r"proposal|straw|issue.?paper", text):
            return "proposal"
        elif re.search(r"comment", text):
            return "comment"
        elif re.search(r"tariff|amendment", text):
            return "tariff"
        elif re.search(r"decision|memo", text):
            return "decision"
        return "other"

    def _sanitize_committee_slug(self, committee_name):
        """Turn a committee name into a filesystem-safe slug."""
        if not committee_name:
            return "general"
        slug = re.sub(r"[^\w\s-]", "", committee_name.lower())
        slug = re.sub(r"[\s]+", "-", slug.strip())
        return slug[:50]

    def _download_document(self, url, committee, meeting_date):
        """
        Download a document to the local docs directory.
        Returns the local path or None on failure.
        """
        try:
            self._polite_delay()

            # Build local path: docs/{rto}/{committee-slug}/{YYYY-MM}/{filename}
            parsed = urlparse(url)
            filename = unquote(parsed.path.split("/")[-1])
            if not filename or filename == "/":
                # Generate a filename from URL hash
                filename = hashlib.md5(url.encode()).hexdigest()[:12] + ".pdf"

            date_folder = meeting_date[:7]  # YYYY-MM
            committee_slug = self._sanitize_committee_slug(committee)
            local_dir = (
                self.DOCS_DIR
                / self.rto_name.lower()
                / committee_slug
                / date_folder
            )
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path = local_dir / filename

            if local_path.exists():
                print(f"      Already have: {filename}")
                return str(local_path), None  # text already in DB

            resp = self.session.get(url, timeout=30, stream=True)
            resp.raise_for_status()

            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            size_kb = os.path.getsize(local_path) / 1024
            print(f"      Downloaded: {filename} ({size_kb:.0f} KB)")

            # Extract text immediately so it's ready for AI screening
            text = self._extract_text(local_path)
            if text:
                print(f"      Extracted: {len(text):,} chars of text")

            return str(local_path), text

        except Exception as e:
            print(f"      Download failed ({url}): {e}")
            return None, None

    def _extract_text(self, local_path):
        """
        Extract plain text from a downloaded file.
        Supports PDF (via pdfplumber) and plain text files.
        Returns a string or None if extraction fails/unsupported.
        """
        local_path = Path(local_path)
        suffix = local_path.suffix.lower()

        if suffix == ".pdf" and _PDFPLUMBER_AVAILABLE:
            try:
                pages = []
                with pdfplumber.open(str(local_path)) as pdf:
                    for page in pdf.pages[:50]:  # cap at 50 pages
                        text = page.extract_text()
                        if text:
                            pages.append(text)
                return "\n\n".join(pages) if pages else None
            except Exception as e:
                print(f"      PDF text extraction failed: {e}")
                return None

        if suffix in (".txt", ".csv"):
            try:
                return local_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return None

        # xlsx/pptx/docx — skip for now, handle later
        return None

    def _head_check(self, url):
        """Check if a URL exists via HEAD request. Returns True/False."""
        try:
            self._polite_delay()
            resp = self.session.head(url, timeout=10, allow_redirects=True)
            return resp.status_code == 200
        except Exception:
            return False
