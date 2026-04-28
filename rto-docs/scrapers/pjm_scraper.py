#!/usr/bin/env python3
"""
PJM Document Scraper

Scrapes meeting schedules and documents from PJM's website.

── Architecture (confirmed via live Chrome browse 2026-04-05) ──

PJM Calendar (https://www.pjm.com/calendar):
  - FullCalendar.js grid: events are `.fc-event` DIVs inside `td.fc-day` cells
  - Events show abbreviated names only ("MIC", "OC", "TEAC")
  - NO data attributes on the events — date comes from parent td.fc-day
  - Clicking an event populates a sidebar panel (parent has class
    containing "event-details") with:
      • Full committee name
      • Date and time
      • "View posted materials" link → GUID-based URL:
        /forms/registration/Meeting%20Registration.aspx?ID={GUID}

Materials Page (the GUID URL):
  - Page title: "Meeting Details"
  - Collapsible jQuery UI accordion sections, one per meeting
  - Section header shows: "{Committee Name} {M.D.YYYY}"
  - Expanding a section reveals a document table:
      • Rows: <tr> with <td> cells: [checkbox, date, link+title]
      • Document links: <a href="/-/media/DotCom/committees-groups/...">
      • ~54 documents found on a single MIC page
  - URL pattern for docs:
    https://www.pjm.com/-/media/DotCom/committees-groups/committees/
    {abbrev}/{year}/{date}/{date}-{filename}.ashx (or .pdf)

Fallback Strategy:
  - Construct predictable URLs for known committees:
    /committees-groups/committees/{slug}/{year}/{YYYYMMDD}/{YYYYMMDD}-agenda.pdf
  - Send HEAD requests to verify existence before downloading
"""

import re
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, unquote

from bs4 import BeautifulSoup

from .base_scraper import BaseRTOScraper


class PJMScraper(BaseRTOScraper):

    BASE_URL = "https://www.pjm.com"
    CALENDAR_URL = "https://www.pjm.com/calendar"
    MATERIALS_BASE = "https://www.pjm.com/forms/registration/Meeting%20Registration.aspx"

    # ── Committee mappings ──────────────────────────────────────

    # Primary committees: name → URL slug
    COMMITTEE_SLUGS = {
        "Members Committee": "mc",
        "Markets and Reliability Committee": "mrc",
        "Market Implementation Committee": "mic",
        "Planning Committee": "pc",
        "Operating Committee": "oc",
        "Risk Management Committee": "rmc",
        "Transmission Expansion Advisory Committee": "teac",
        "Finance Committee": "fc",
        "Audit Advisory Committee": "aac",
        "Liaison Committee": "lc",
        "Nominating Committee": "nc",
    }

    # Subcommittees / task forces: name → path segment
    SUBCOMMITTEE_SLUGS = {
        "Cost Development Subcommittee": "subcommittees/cds",
        "Distributed Resources Subcommittee": "subcommittees/disrs",
        "Interconnection Process Subcommittee": "subcommittees/ips",
        "Load Analysis Subcommittee": "subcommittees/las",
        "Resource Adequacy Analysis Subcommittee": "subcommittees/raas",
        "Resource Adequacy Senior Task Force": "task-forces/rastf",
        "Effective Load Carrying Capability Senior Task Force": "task-forces/elccstf",
        "Regulation Market Design Senior Task Force": "task-forces/rmdstf",
    }

    # Abbreviations the calendar grid uses → full names
    ABBREV_TO_COMMITTEE = {
        "MC": "Members Committee",
        "MRC": "Markets and Reliability Committee",
        "MIC": "Market Implementation Committee",
        "PC": "Planning Committee",
        "OC": "Operating Committee",
        "RMC": "Risk Management Committee",
        "TEAC": "Transmission Expansion Advisory Committee",
        "FC": "Finance Committee",
        "AAC": "Audit Advisory Committee",
        "LC": "Liaison Committee",
        "NC": "Nominating Committee",
        "CDS": "Cost Development Subcommittee",
        "DISRS": "Distributed Resources Subcommittee",
        "IPS": "Interconnection Process Subcommittee",
        "LAS": "Load Analysis Subcommittee",
        "RAAS": "Resource Adequacy Analysis Subcommittee",
        "RASTF": "Resource Adequacy Senior Task Force",
    }

    # Doc-type classification patterns (PJM-specific)
    DOC_TYPE_PATTERNS = {
        "agenda": r"agenda",
        "minutes": r"minutes|draft-minutes",
        "presentation": r"presentation|slide|briefing",
        "report": r"report|summary|update|review",
        "vote": r"vote|poll|ballot|motion",
        "manual": r"manual|m\d{2}",
        "matrix": r"matrix",
        "issue-charge": r"issue.?charge|problem.?statement",
    }

    @property
    def rto_name(self):
        return "PJM"

    # ── Meeting Discovery ───────────────────────────────────────

    def scrape_meetings(self, lookback_days=14, lookahead_days=30):
        """
        Scrape PJM's calendar using Playwright to interact with the
        FullCalendar grid.

        Strategy:
          1. Load calendar page
          2. Click each .fc-event in the grid
          3. Read the sidebar panel for committee name, date, and
             the "View posted materials" GUID link
          4. If Playwright fails, fall back to constructing meeting
             URLs from known committee schedules
        """
        start_date, end_date = self._date_range(lookback_days, lookahead_days)
        meetings = []

        print(f"  Scraping PJM calendar ({start_date} to {end_date})...")

        try:
            from playwright.sync_api import sync_playwright
            meetings = self._scrape_via_playwright(start_date, end_date)
        except ImportError:
            print("  Playwright not installed, using fallback strategy...")
        except Exception as e:
            print(f"  Playwright error: {e}")

        # Fallback: construct meeting list from known committee schedules
        if not meetings:
            print("  Using constructed URL fallback...")
            meetings = self._construct_meeting_list(start_date, end_date)

        return meetings

    def _scrape_via_playwright(self, start_date, end_date):
        """
        Use Playwright to scrape PJM's FullCalendar grid.

        Strategy:
          1. Load the calendar (defaults to current month view)
          2. Use JS to spatially map each .fc-event to a td.fc-day[data-date]
             by comparing horizontal bounding-rect positions — events are
             absolutely positioned inside the grid, not DOM-children of the td.
          3. Only click events whose mapped date falls in [start_date, end_date]
          4. After each click, read the expanded <li class="event expanded">
             for committee name, time, and materials URL.
        """
        from playwright.sync_api import sync_playwright

        meetings = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            try:
                page.goto(self.CALENDAR_URL,
                          wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(3000)

                # Navigate through each required month
                months_to_check = self._months_in_range_from_page(
                    page, start_date, end_date
                )

                for month_offset in months_to_check:
                    self._navigate_calendar(page, month_offset)

                    # Spatially map events to dates using bounding rects
                    event_date_map = self._map_events_to_dates(page)
                    in_range = [
                        (idx, date) for idx, date in event_date_map
                        if start_date <= date <= end_date
                    ]
                    print(f"    {len(event_date_map)} events mapped; "
                          f"{len(in_range)} in range")

                    for event_idx, event_date in in_range:
                        meeting = self._click_and_parse(
                            page, event_idx, event_date
                        )
                        if meeting:
                            key = (meeting["title"], meeting["meeting_date"])
                            if not any(
                                (m["title"], m["meeting_date"]) == key
                                for m in meetings
                            ):
                                meetings.append(meeting)

            except Exception as e:
                print(f"    Playwright calendar parse error: {e}")
            finally:
                browser.close()

        return meetings

    def _months_in_range_from_page(self, page, start_date, end_date):
        """
        Determine month navigation offsets relative to the currently
        displayed calendar month (read from the page, not the system clock).
        """
        # Read displayed month from the page content
        page_text = page.evaluate(
            "document.querySelector('#calendar, .fc') "
            "? document.querySelector('#calendar, .fc').innerText.substring(0,100) "
            ": ''"
        )
        # Try to parse "Month YYYY" from the calendar header area
        current_year, current_month = datetime.now().year, datetime.now().month
        m = re.search(
            r"(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{4})",
            page_text, re.IGNORECASE
        )
        if m:
            try:
                dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%B %Y")
                current_year, current_month = dt.year, dt.month
            except ValueError:
                pass

        current_month_num = current_year * 12 + current_month
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        start_month_num = start_dt.year * 12 + start_dt.month
        end_month_num = end_dt.year * 12 + end_dt.month

        return list(range(
            start_month_num - current_month_num,
            end_month_num - current_month_num + 1
        ))

    def _navigate_calendar(self, page, month_offset):
        """Navigate the FullCalendar by month_offset steps."""
        if month_offset == 0:
            return
        direction = "prev" if month_offset < 0 else "next"
        selector = (
            f".fc-{direction}-button, .fc-button-{direction}, "
            f"button[aria-label='{direction}']"
        )
        for _ in range(abs(month_offset)):
            btn = page.query_selector(selector)
            if btn:
                btn.click()
                page.wait_for_timeout(1500)

    def _map_events_to_dates(self, page):
        """
        Use JS bounding-rect comparison to map each .fc-event to a date.

        Returns list of (event_index, date_string) for non-multi-day events.
        The calendar renders events as absolutely positioned divs inside the
        grid; their horizontal center falls within a td.fc-day[data-date] column.
        """
        result = page.evaluate("""
            () => {
                const tdCells = Array.from(
                    document.querySelectorAll('td.fc-day[data-date]')
                ).map(td => {
                    const r = td.getBoundingClientRect();
                    return { date: td.getAttribute('data-date'), left: r.left, right: r.right };
                });

                const events = Array.from(document.querySelectorAll('.fc-event'));
                const mapped = [];
                events.forEach((ev, idx) => {
                    const cls = ev.className || '';
                    if (cls.includes('fc-event-multi-days')) return;
                    const r = ev.getBoundingClientRect();
                    if (r.width === 0 && r.height === 0) return;
                    const cx = (r.left + r.right) / 2;
                    const match = tdCells.find(td => cx >= td.left && cx < td.right);
                    if (match) mapped.push([idx, match.date]);
                });
                return mapped;
            }
        """)
        return [(int(idx), date) for idx, date in result]

    def _click_and_parse(self, page, event_idx, event_date):
        """
        Click the event at event_idx in .fc-event NodeList and parse the
        expanded sidebar entry for committee name, time, and materials URL.
        """
        try:
            current_events = page.query_selector_all(".fc-event")
            if event_idx >= len(current_events):
                return None
            ev = current_events[event_idx]
            abbrev = ev.inner_text().strip()

            ev.click()
            page.wait_for_timeout(800)

            data = page.evaluate("""
                () => {
                    const li = document.querySelector('li.event.expanded');
                    if (!li) return null;
                    const a = li.querySelector('a[href*="Registration.aspx"]')
                            || li.querySelector('.event-details a');
                    return {
                        text: li.innerText,
                        href: a ? a.getAttribute('href') : null
                    };
                }
            """)

            if not data:
                return None

            panel_text = data.get("text", "")
            materials_href = data.get("href")

            committee_name = self._identify_committee_from_text(panel_text)
            if not committee_name and abbrev:
                committee_name = self.ABBREV_TO_COMMITTEE.get(
                    abbrev.upper(), abbrev
                )

            meeting_time = self._extract_time_from_text(panel_text)
            materials_url = (
                urljoin(self.BASE_URL, materials_href)
                if materials_href else None
            )
            title = f"{committee_name} Meeting" if committee_name else abbrev

            return {
                "title": title,
                "meeting_date": event_date,
                "meeting_time": meeting_time,
                "committee": committee_name,
                "source_url": self.CALENDAR_URL,
                "detail_url": None,
                "materials_url": materials_url,
            }

        except Exception as e:
            print(f"      Error clicking event {event_idx}: {e}")
            return None

    def _construct_meeting_list(self, start_date, end_date):
        """
        Fallback: construct a meeting list by probing known committee
        page URLs. PJM committees meet on predictable schedules
        (monthly, with some meeting the same week).
        """
        meetings = []
        all_committees = {**self.COMMITTEE_SLUGS, **self.SUBCOMMITTEE_SLUGS}

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        # For each committee, try the committee landing page which
        # often lists upcoming meeting dates
        for committee_name, slug in all_committees.items():
            committee_url = (
                f"{self.BASE_URL}/committees-and-groups/"
                f"committees/{slug}" if "/" not in slug
                else f"{self.BASE_URL}/committees-and-groups/{slug}"
            )

            try:
                self._polite_delay()
                resp = self.session.get(committee_url, timeout=8)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "lxml")

                # Look for meeting date references in the page
                # PJM committee pages sometimes list upcoming meetings
                text = soup.get_text()
                dates_found = re.findall(
                    r"(\d{1,2}/\d{1,2}/\d{4}|\w+ \d{1,2},? \d{4})",
                    text
                )

                for date_str in dates_found:
                    parsed_date = self._try_parse_date(date_str)
                    if parsed_date and start_date <= parsed_date <= end_date:
                        meetings.append({
                            "title": f"{committee_name} Meeting",
                            "meeting_date": parsed_date,
                            "committee": committee_name,
                            "source_url": committee_url,
                            "detail_url": committee_url,
                            "materials_url": None,
                        })

            except Exception as e:
                print(f"    Error checking {committee_name}: {e}")

        return meetings

    # ── Document Discovery ──────────────────────────────────────

    def scrape_meeting_documents(self, meeting_info):
        """
        Find documents for a PJM meeting.

        Strategy (in priority order):
          1. If we have a materials_url (GUID page), scrape the
             accordion sections for document table rows
          2. Probe predictable URLs for known doc types
          3. Scrape the committee landing page for any linked docs
        """
        documents = []

        # Strategy 1: Materials page (GUID-based)
        materials_url = meeting_info.get("materials_url")
        if materials_url:
            documents = self._scrape_materials_page(materials_url,
                                                     meeting_info)

        # Strategy 2: URL probing fallback
        if not documents:
            documents = self._probe_document_urls(meeting_info)

        return documents

    def _scrape_materials_page(self, materials_url, meeting_info):
        """
        Scrape the GUID-based Meeting Registration / Materials page.

        Page structure (from live Chrome browse):
          - jQuery UI accordion with collapsible sections
          - Each section header: "{Committee Name} {M.D.YYYY}"
          - Expanded section contains a <table> with document rows
          - Each row: <td>[checkbox]</td> <td>[date]</td>
                      <td><a href="/-/media/...">Title</a></td>
        """
        documents = []

        try:
            self._polite_delay()
            resp = self.session.get(materials_url, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # Find all document links on the page
            # PJM uses /-/media/ paths for hosted documents
            doc_links = soup.select(
                'a[href*="/-/media/"], '
                'a[href$=".pdf"], '
                'a[href$=".xlsx"], '
                'a[href$=".xls"], '
                'a[href$=".pptx"], '
                'a[href$=".docx"], '
                'a[href$=".ashx"]'
            )

            print(f"    Materials page: {len(doc_links)} doc links found")

            for link in doc_links:
                href = link.get("href", "")
                full_url = urljoin(self.BASE_URL, href)

                # Skip non-document links
                parsed = urlparse(full_url)
                if "pjm.com" not in parsed.netloc:
                    continue

                title = link.get_text(strip=True)
                filename = unquote(full_url.split("/")[-1])

                # Try to get the posted date from the sibling <td>
                posted_date = None
                parent_row = link.find_parent("tr")
                if parent_row:
                    cells = parent_row.find_all("td")
                    if len(cells) >= 2:
                        date_text = cells[1].get_text(strip=True)
                        posted_date = self._try_parse_date(date_text)

                doc_type = self._classify_pjm_doc(filename, title)

                documents.append({
                    "download_url": full_url,
                    "doc_type": doc_type,
                    "title": title or filename,
                    "filename": filename,
                    "posted_date": posted_date,
                })

        except Exception as e:
            print(f"    Error scraping materials page: {e}")

        return documents

    def _probe_document_urls(self, meeting_info):
        """
        Construct and probe predictable PJM document URLs.

        Pattern:
          https://www.pjm.com/-/media/DotCom/committees-groups/
          committees/{slug}/{year}/{YYYYMMDD}/{YYYYMMDD}-{doctype}.pdf
        """
        documents = []
        committee = meeting_info.get("committee", "")
        meeting_date = meeting_info.get("meeting_date", "")

        if not meeting_date:
            return documents

        # Find the URL slug for this committee
        slug = self.COMMITTEE_SLUGS.get(committee)
        if not slug:
            slug = self.SUBCOMMITTEE_SLUGS.get(committee)
        if not slug:
            return documents

        date_str = meeting_date.replace("-", "")  # YYYYMMDD
        year = meeting_date[:4]

        # Determine base path segment
        if "/" in (self.SUBCOMMITTEE_SLUGS.get(committee, "")):
            path_segment = self.SUBCOMMITTEE_SLUGS[committee]
        else:
            path_segment = f"committees/{slug}"

        base_url = (
            f"{self.BASE_URL}/-/media/DotCom/committees-groups/"
            f"{path_segment}/{year}/{date_str}/{date_str}"
        )

        # Probe common document names
        doc_names = [
            ("agenda", "agenda.pdf"),
            ("minutes", "minutes.pdf"),
            ("minutes", "draft-minutes.pdf"),
            ("presentation", "presentation.pdf"),
            ("consent-agenda", "consent-agenda.pdf"),
            ("report", "report.pdf"),
            ("vote", "poll-results.pdf"),
            ("matrix", "matrix.pdf"),
        ]

        for doc_type, suffix in doc_names:
            url = f"{base_url}-{suffix}"
            if self._head_check(url):
                filename = f"{date_str}-{suffix}"
                documents.append({
                    "download_url": url,
                    "doc_type": doc_type,
                    "title": f"{committee} {suffix.replace('.pdf', '').replace('-', ' ').title()}",
                    "filename": filename,
                })

            # Also try .ashx extension (PJM uses both)
            url_ashx = url.replace(".pdf", ".ashx")
            if url_ashx != url and self._head_check(url_ashx):
                filename = f"{date_str}-{suffix.replace('.pdf', '.ashx')}"
                documents.append({
                    "download_url": url_ashx,
                    "doc_type": doc_type,
                    "title": f"{committee} {suffix.replace('.pdf', '').replace('-', ' ').title()}",
                    "filename": filename,
                })

        return documents

    # ── PJM-specific helpers ────────────────────────────────────

    def _classify_pjm_doc(self, filename, title=""):
        """Classify a PJM document type using PJM-specific patterns."""
        text = f"{filename} {title}".lower()
        for doc_type, pattern in self.DOC_TYPE_PATTERNS.items():
            if re.search(pattern, text):
                return doc_type
        return self._classify_doc(f"{filename} {title}")

    def _identify_committee_from_text(self, text):
        """Match a full committee name from free text."""
        if not text:
            return None
        for name in self.COMMITTEE_SLUGS:
            if name.lower() in text.lower():
                return name
        for name in self.SUBCOMMITTEE_SLUGS:
            if name.lower() in text.lower():
                return name
        return None

    def _identify_committee(self, title):
        """
        Match a meeting title to a known committee.
        Returns (committee_name, url_slug) or (None, None).
        """
        if not title:
            return None, None

        title_lower = title.lower()

        # Check primary committees
        for name, slug in self.COMMITTEE_SLUGS.items():
            if name.lower() in title_lower:
                return name, slug

        # Check subcommittees
        for name, slug in self.SUBCOMMITTEE_SLUGS.items():
            if name.lower() in title_lower:
                return name, slug

        # Check abbreviations
        for abbrev, name in self.ABBREV_TO_COMMITTEE.items():
            # Match abbreviation as a standalone word
            if re.search(rf"\b{re.escape(abbrev)}\b", title, re.IGNORECASE):
                slug = self.COMMITTEE_SLUGS.get(
                    name, self.SUBCOMMITTEE_SLUGS.get(name)
                )
                return name, slug

        return None, None

    def _extract_date_from_text(self, text):
        """Extract a date from free text, return YYYY-MM-DD or None."""
        # Try M/D/YYYY
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if m:
            try:
                dt = datetime(int(m.group(3)), int(m.group(1)),
                              int(m.group(2)))
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # Try M.D.YYYY (PJM uses this on materials pages)
        m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
        if m:
            try:
                dt = datetime(int(m.group(3)), int(m.group(1)),
                              int(m.group(2)))
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # Try "Month DD, YYYY"
        m = re.search(
            r"(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
            text, re.IGNORECASE
        )
        if m:
            try:
                dt = datetime.strptime(
                    f"{m.group(1)} {m.group(2)} {m.group(3)}",
                    "%B %d %Y"
                )
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        return None

    def _extract_time_from_text(self, text):
        """Extract a time string from free text. Handles AM/PM and a.m./p.m."""
        # Match "9:00 a.m. - 12:00 p.m." or "9:00 AM ET"
        m = re.search(
            r"(\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.|AM|PM|am|pm)"
            r"(?:\s*[-\u2013]\s*\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.|AM|PM|am|pm))?"
            r"(?:\s*(?:E[PDS]T|ET))?)",
            text
        )
        return m.group(1).strip() if m else None

    def _try_parse_date(self, date_str):
        """Try various date formats, return YYYY-MM-DD or None."""
        if not date_str:
            return None

        formats = [
            "%m/%d/%Y",
            "%m.%d.%Y",
            "%B %d, %Y",
            "%B %d %Y",
            "%b %d, %Y",
            "%b %d %Y",
            "%Y-%m-%d",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt).strftime(
                    "%Y-%m-%d"
                )
            except ValueError:
                continue
        return None


def main():
    """Run the PJM scraper standalone."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from db.database import init_db

    init_db()

    scraper = PJMScraper()
    scraper.run(
        lookback_days=14,
        lookahead_days=30,
        download=True,
    )


if __name__ == "__main__":
    main()
