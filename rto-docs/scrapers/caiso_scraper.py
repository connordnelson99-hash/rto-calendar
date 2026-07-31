#!/usr/bin/env python3
"""
CAISO Document Scraper

Scrapes meeting schedules and documents from CAISO's website.

── Architecture (confirmed via live Chrome browse 2026-04-05) ──

Meetings Page (https://www.caiso.com/meetings-events/meetings):
  - "Upcoming meetings" carousel: div.events-upcoming.card-column-group
  - Each meeting is a <button class="card"> inside div.col.tns-item slides
  - Card data attributes:
      • data-event-id: numeric event ID (e.g., "222093")
      • data-bs-toggle="modal" / data-bs-target="#eventUpcomingModal-{id}"
      • data-event-docs-sort-json: JSON array of attached docs (often "[]"
        for future meetings; populated for past meetings)
  - Date/time in element with class "event-date-time":
      format "04/06/2026\\n9:00 AM - 10:00 AM"
  - Meeting type tags: <span class="tag online"> or <span class="tag deadline">
  - Clicking a card opens a Bootstrap modal with:
      • Event description
      • Webex link
      • "View more events under {topic}" → links to stakeholder center
      • "Add to calendar" → /resources/export/ical?id={eventId}
      • Event URL → /meetings-events/calendar/{slug}

  - "Meetings by topic" section: 91 topic groups
      • Each in a <div class="event-group"> with <h3 class="subjects">
      • Links to stakeholdercenter.caiso.com initiative pages

Stakeholder Center Initiative Pages
  (stakeholdercenter.caiso.com/StakeholderInitiatives/{slug}):
  - THIS IS WHERE ALL DOCUMENTS LIVE
  - Individual event pages do NOT contain document links
  - Phase table: <table class="table table-bordered table-phase">
      • 3-column rows: [meeting info], [document links], [comments]
      • Meeting info cell includes date, time, and "Details" link to
        caiso.com/meetings-events/calendar/{slug}
      • Document links:
          - PDFs at stakeholdercenter.caiso.com/InitiativeDocuments/{name}.pdf
          - XLS at caiso.com/documents/{name}.xlsx
          - Also YouTube video links (to skip)
  - Status table: <table class="table table-bordered table-status">
      • Timeline/milestone data

Individual Event Pages (caiso.com/meetings-events/calendar/{slug}):
  - Description, webex info, contact info ONLY
  - NO document downloads
"""

import os
import re
import json
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, unquote

from bs4 import BeautifulSoup

from .base_scraper import BaseRTOScraper


class CAISOScraper(BaseRTOScraper):

    BASE_URL = "https://www.caiso.com"
    MEETINGS_URL = "https://www.caiso.com/meetings-events/meetings"
    STAKEHOLDER_URL = "https://stakeholdercenter.caiso.com"
    # The public meetings page only server-renders a ~5-card "Upcoming meetings"
    # carousel, so scraping it capped CAISO's lookahead at a few days while the
    # other RTOs published their full forward calendars. This is the FullCalendar
    # feed the calendar view (caiso.com/meetings-events/calendar) actually loads:
    # it takes start=/end= ISO dates and returns every event in the window, each
    # with its documents inline in a `docsSort` tree. Confirmed live 2026-07-10.
    CALENDAR_JSON_URL = "https://www.caiso.com/resources/calendar.json"

    # Known meeting series / governing bodies
    MEETING_SERIES = {
        "Board of Governors": "board-of-governors",
        "EIM Governing Body": "eim-governing-body",
        "Stakeholder Symposium": "stakeholder-symposium",
        "Market Surveillance Committee": "market-surveillance-committee",
    }

    # Hydropower & storage relevant initiative keywords for filtering
    HYDRO_STORAGE_KEYWORDS = [
        "energy storage", "hydro", "pumped", "storage enhancements",
        "resource adequacy", "capacity", "ancillary service",
        "state of charge", "day-ahead market", "extended day-ahead",
        "edam", "dame", "real-time market", "transmission planning",
        "interconnection", "reliability", "generator",
    ]

    @property
    def rto_name(self):
        return "CAISO"

    # ── Meeting Discovery ───────────────────────────────────────

    def scrape_meetings(self, lookback_days=14, lookahead_days=30):
        """
        Discover CAISO meetings for the lookback/lookahead window.

        Primary source is the calendar JSON feed (CALENDAR_JSON_URL), which
        returns the full forward calendar plus each event's documents inline.
        If that feed is empty or fails (e.g. CAISO changes the endpoint), fall
        back to the legacy path: the meetings-page carousel plus stakeholder
        center initiative tables.
        """
        start_date, end_date = self._date_range(lookback_days, lookahead_days)

        print(f"  Scraping CAISO meetings ({start_date} to {end_date})...")

        # Primary: the FullCalendar JSON feed (full window + inline docs)
        meetings = self._scrape_calendar_json(start_date, end_date)
        if meetings:
            print(f"  Found {len(meetings)} CAISO meetings (calendar.json)")
            return meetings

        # Fallback: the old carousel + stakeholder-center topic scrape. Only
        # reaches ~5 upcoming meetings, but keeps some data flowing if the feed
        # changes shape or a bot filter blocks it.
        print("    calendar.json returned nothing; falling back to the "
              "meetings-page carousel")
        meetings = self._scrape_meetings_listing(start_date, end_date)

        topic_meetings = self._scrape_topic_meetings(start_date, end_date)
        for tm in topic_meetings:
            # Deduplicate by title + date
            key = (tm["title"], tm["meeting_date"])
            if not any((m["title"], m["meeting_date"]) == key
                       for m in meetings):
                meetings.append(tm)

        print(f"  Found {len(meetings)} CAISO meetings (fallback)")
        return meetings

    # ── Calendar JSON feed (primary discovery) ──────────────────

    def _scrape_calendar_json(self, start_date, end_date):
        """
        Pull every event in the window from the FullCalendar JSON feed.

        The feed requires both start= and end= (ISO YYYY-MM-DD); a bare request
        returns []. Each event carries its documents inline as a `docsSort`
        tree, so most meetings need no follow-up page fetch for attachments.
        """
        try:
            self._polite_delay()
            resp = self.session.get(
                self.CALENDAR_JSON_URL,
                params={"start": start_date, "end": end_date},
                headers={"Accept": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            events = resp.json()
        except Exception as e:
            print(f"    Error fetching CAISO calendar.json: {e}")
            return []

        if not isinstance(events, list):
            print(f"    Unexpected calendar.json shape: {type(events).__name__}")
            return []

        print(f"    calendar.json: {len(events)} events in window")

        meetings = []
        for ev in events:
            parsed = self._parse_calendar_event(ev, start_date, end_date)
            if parsed:
                meetings.append(parsed)
        return meetings

    def _parse_calendar_event(self, ev, start_date, end_date):
        """Turn one calendar.json event into a meeting dict."""
        try:
            if not isinstance(ev, dict):
                return None
            if ev.get("hidden") or ev.get("isoArchived"):
                return None

            fmt = ev.get("format") or []
            # Skip holidays; they are calendar noise, not stakeholder meetings.
            if any((f or "").lower() == "holiday" for f in fmt):
                return None

            start = (ev.get("start") or "")
            meeting_date = start[:10]
            if not re.match(r"\d{4}-\d{2}-\d{2}$", meeting_date):
                return None
            if not (start_date <= meeting_date <= end_date):
                return None

            title = (ev.get("title") or "").strip() or "CAISO Meeting"

            topics = (ev.get("calendarTopics") or {}).get("titles") or []
            categories = (ev.get("calendarCategories") or {}).get("titles") or []
            committee = topics[0] if topics else (categories[0] if categories else None)

            documents = self._parse_docssort(ev.get("docsSort"))

            return {
                "title": title,
                "meeting_date": meeting_date,
                "meeting_time": self._format_event_time(ev),
                "committee": committee,
                "meeting_type": fmt[0] if fmt else None,
                "source_url": self.MEETINGS_URL,
                "detail_url": ev.get("url"),
                "materials_url": self._initiative_url_from_event(ev),
                "event_id": ev.get("id"),
                "embedded_docs": [],
                # Docs parsed from the feed. If empty, scrape_meeting_documents
                # falls back to the linked initiative/topic page (docs for a
                # future meeting are often posted there after the feed lists it).
                "_documents": documents,
            }
        except Exception as e:
            print(f"      Error parsing calendar event: {e}")
            return None

    def _parse_docssort(self, raw):
        """
        Flatten a `docsSort` value into a list of document dicts.

        `docsSort` is a (sometimes JSON-encoded) tree of Folder/Document nodes.
        A Document's real download URL lives at data.url, or failing that the
        first entry of data.siteInfos (siteId 1 is the caiso.com copy).
        """
        if not raw:
            return []
        nodes = raw
        if isinstance(raw, str):
            try:
                nodes = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return []

        out = []
        seen = set()
        self._walk_doc_nodes(nodes, out, seen)
        return out

    def _walk_doc_nodes(self, nodes, out, seen):
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            children = node.get("children") or []
            if (node.get("type") or "").lower() == "folder":
                self._walk_doc_nodes(children, out, seen)
                continue

            data = node.get("data") or {}
            url = data.get("url")
            if not url:
                site_infos = data.get("siteInfos") or []
                # Prefer the caiso.com copy (siteId 1), else any populated URL.
                for want_id in (1, None):
                    for si in site_infos:
                        if not si.get("url"):
                            continue
                        if want_id is None or si.get("siteId") == want_id:
                            url = si["url"]
                            break
                    if url:
                        break

            if url:
                full_url = url if url.startswith("http") else urljoin(self.BASE_URL, url)
                if full_url not in seen and "youtu" not in full_url.lower():
                    seen.add(full_url)
                    filename = data.get("filename") or unquote(full_url.split("/")[-1])
                    title = data.get("title") or node.get("label") or filename
                    posted = data.get("isoDatePosted")
                    posted_date = (
                        posted[:10]
                        if isinstance(posted, str) and len(posted) >= 10
                        else None
                    )
                    out.append({
                        "download_url": full_url,
                        "doc_type": self._classify_caiso_doc(filename, title),
                        "title": title,
                        "filename": filename,
                        "posted_date": posted_date,
                    })

            # A node can carry both a document and nested children.
            self._walk_doc_nodes(children, out, seen)

    def _initiative_url_from_event(self, ev):
        """
        Best materials URL for an event: the stakeholder center initiative page
        linked in the description (has the phase table + late-posted docs), else
        the calendar topic page.
        """
        desc = ev.get("description") or ""
        m = re.search(
            r"https://stakeholdercenter\.caiso\.com/StakeholderInitiatives/[^\s\"'<>]+",
            desc,
        )
        if m:
            return m.group(0)

        topic_urls = ev.get("calendarTopicsUrl") or []
        if topic_urls and topic_urls[0]:
            url = topic_urls[0]
            return url if url.startswith("http") else urljoin(self.BASE_URL, url)
        return None

    def _format_event_time(self, ev):
        """Render 'start - end PT' from an event, or None for all-day items."""
        if ev.get("allDay"):
            return None

        def clock(iso):
            try:
                t = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
            except (ValueError, TypeError):
                return None
            fmt = "%#I:%M %p" if os.name == "nt" else "%-I:%M %p"
            return t.strftime(fmt)

        start = clock(ev.get("start") or "")
        if not start:
            return None
        end = clock(ev.get("end") or "")
        span = f"{start} - {end}" if end else start
        return f"{span} PT"

    def _scrape_meetings_listing(self, start_date, end_date):
        """
        Parse the meetings listing page.

        Structure: <button class="card"> elements with data attributes:
          - data-event-id
          - data-event-docs-sort-json (JSON array of doc info)
          - date/time in child .event-date-time element
        """
        meetings = []

        try:
            self._polite_delay()
            resp = self.session.get(self.MEETINGS_URL, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # Find all meeting cards (button.card with data-event-id)
            cards = soup.select("button.card[data-event-id]")
            print(f"    Meetings page: {len(cards)} card elements found")

            for card in cards:
                meeting = self._parse_meeting_card(card, start_date, end_date)
                if meeting:
                    meetings.append(meeting)

            # Also discover topic links for stakeholder center scraping
            # These are in div.event-group elements
            topic_links = soup.select(
                "div.event-group a[href*='stakeholdercenter']"
            )
            self._topic_urls = [
                a.get("href") for a in topic_links
                if a.get("href")
            ]
            print(f"    Found {len(self._topic_urls)} topic links "
                  f"to stakeholder center")

        except Exception as e:
            print(f"  Error scraping CAISO meetings page: {e}")
            self._topic_urls = []

        return meetings

    def _parse_meeting_card(self, card, start_date, end_date):
        """
        Parse a single <button class="card"> element.

        Current card structure (confirmed 2026-04-08):
          <button class="card" data-event-id="..." data-track-destination="...">
            <div class="card-body">
              <div class="tag-group"><span class="tag online">Online</span></div>
              <div class="event-title">Meeting Title</div>
              <div class="d-none">
                <div class="event-topic">
                  <a href="https://stakeholdercenter.caiso.com/...">...</a>
                </div>
              </div>
            </div>
            <div class="card-footer">
              <div class="event-date-time">
                <div class="event-date d-inline-block me-2">04/08/2026</div>
                <div class="event-time d-inline-block">1:00 PM - 4:00 PM</div>
              </div>
            </div>
          </button>
        """
        try:
            event_id = card.get("data-event-id", "")

            # Title is in div.event-title
            title_el = card.select_one(".event-title")
            if title_el:
                title = title_el.get_text(strip=True)
            else:
                # Fallback: first heading in card-body
                card_body = card.select_one(".card-body")
                title_el = card_body.select_one("h5, h4, h3") if card_body else None
                title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:100]

            # Date and time are in separate child divs inside .event-date-time
            date_el = card.select_one(".event-date")
            time_el = card.select_one(".event-time")

            if not date_el:
                return None

            meeting_date = self._extract_date_from_text(date_el.get_text(strip=True))
            if not meeting_date:
                return None
            if not (start_date <= meeting_date <= end_date):
                return None

            meeting_time = time_el.get_text(strip=True) if time_el else None

            # Committee/topic name and materials URL from div.event-topic > a
            # Link text is "View more events under {topic name}"
            # href may point to stakeholdercenter.caiso.com (has doc pages)
            # or caiso.com/meetings-events/topics/ (topic listing page)
            topic_link = card.select_one(".event-topic a")
            committee = None
            materials_url = None
            if topic_link:
                href = topic_link.get("href", "")
                link_text = topic_link.get_text(strip=True)
                prefix = "View more events under "
                committee = (
                    link_text[len(prefix):].strip()
                    if link_text.startswith(prefix)
                    else link_text.strip()
                )
                # Use any topic URL as materials source
                # stakeholdercenter pages → _scrape_initiative_docs_for_date
                # caiso.com/meetings-events/topics pages → _scrape_page_for_docs
                if href:
                    materials_url = href

            # Event detail URL from data-track-destination
            track_dest = card.get("data-track-destination", "")
            event_url = (
                f"{self.BASE_URL}/meetings-events/calendar/{track_dest}"
                if track_dest else None
            )

            # Check for embedded documents JSON
            docs_json_str = card.get("data-event-docs-sort-json", "[]")
            embedded_docs = []
            try:
                embedded_docs = json.loads(docs_json_str) if docs_json_str else []
            except (json.JSONDecodeError, TypeError):
                pass

            tag_el = card.select_one(".tag")
            meeting_type = tag_el.get_text(strip=True) if tag_el else None

            return {
                "title": title,
                "meeting_date": meeting_date,
                "meeting_time": meeting_time,
                "committee": committee,
                "meeting_type": meeting_type,
                "source_url": self.MEETINGS_URL,
                "detail_url": event_url,
                "materials_url": materials_url,
                "event_id": event_id,
                "embedded_docs": embedded_docs,
            }

        except Exception as e:
            print(f"      Error parsing card: {e}")
            return None

    def _scrape_topic_meetings(self, start_date, end_date):
        """
        Scrape stakeholder center initiative pages found via the
        "Meetings by topic" section.

        Each initiative page has a table.table-phase with meeting rows
        containing document links.
        """
        meetings = []
        topic_urls = getattr(self, "_topic_urls", [])

        # Filter to hydro/storage relevant topics first
        relevant_urls = []
        for url in topic_urls:
            url_lower = url.lower()
            if any(kw in url_lower for kw in self.HYDRO_STORAGE_KEYWORDS):
                relevant_urls.append(url)

        if not relevant_urls:
            relevant_urls = topic_urls[:20]

        print(f"    Scraping {len(relevant_urls)} stakeholder center "
              f"initiative pages...")

        for topic_url in relevant_urls:
            try:
                topic_meetings = self._scrape_initiative_page(
                    topic_url, start_date, end_date
                )
                meetings.extend(topic_meetings)
            except Exception as e:
                print(f"      Error on {topic_url}: {e}")

        return meetings

    def _scrape_initiative_page(self, initiative_url, start_date, end_date):
        """
        Scrape a single stakeholder center initiative page.

        Key structure:
          <table class="table table-bordered table-phase">
            <tr>
              <td>  ← meeting info: type, date, time, "Details" link
              <td>  ← document links (PDFs, videos, presentations)
              <td>  ← comment links
            </tr>
          </table>
        """
        meetings = []

        self._polite_delay()
        resp = self.session.get(initiative_url, timeout=20)
        if resp.status_code != 200:
            return meetings

        soup = BeautifulSoup(resp.text, "lxml")

        # Get initiative name from page title
        title_el = soup.select_one("h1, h2")
        initiative_name = ""
        if title_el:
            initiative_name = title_el.get_text(strip=True)
            initiative_name = re.sub(
                r"^INITIATIVE:\s*", "", initiative_name, flags=re.IGNORECASE
            ).strip()

        # Find the phase table
        phase_table = soup.select_one("table.table-phase")
        if not phase_table:
            tables = soup.select("table.table-bordered")
            for t in tables:
                if "table-status" not in (t.get("class") or []):
                    if t.select("a[href*='.pdf'], a[href*='.xlsx']"):
                        phase_table = t
                        break

        if not phase_table:
            return meetings

        rows = phase_table.select("tr")
        for row in rows:
            cells = row.select("td")
            if len(cells) < 2:
                continue

            meeting_cell = cells[0]
            meeting_text = meeting_cell.get_text(strip=True)

            meeting_date = self._extract_date_from_text(meeting_text)
            if not meeting_date:
                continue
            if not (start_date <= meeting_date <= end_date):
                continue

            meeting_time = self._extract_time_from_text(meeting_text)

            detail_link = meeting_cell.select_one(
                "a[href*='meetings-events/calendar']"
            )
            detail_url = None
            if detail_link:
                detail_url = detail_link.get("href", "")
                if not detail_url.startswith("http"):
                    detail_url = urljoin(self.BASE_URL, detail_url)

            doc_cell = cells[1] if len(cells) > 1 else None
            documents = []
            if doc_cell:
                documents = self._extract_docs_from_cell(doc_cell)

            meeting_title = initiative_name or "CAISO Meeting"

            meetings.append({
                "title": meeting_title,
                "meeting_date": meeting_date,
                "meeting_time": meeting_time,
                "committee": initiative_name,
                "source_url": initiative_url,
                "detail_url": detail_url,
                "materials_url": initiative_url,
                "_documents": documents,
            })

        return meetings

    # ── Document Discovery ──────────────────────────────────────

    def scrape_meeting_documents(self, meeting_info):
        """
        Find documents for a CAISO meeting.

        Strategy:
          1. If _documents already attached (from initiative page),
             use those directly
          2. If embedded_docs from card data attribute, parse those
          3. If we have a materials_url (initiative page), re-scrape
          4. Fall back to scraping the detail_url
        """
        # Strategy 1: Pre-scraped from initiative page
        if meeting_info.get("_documents"):
            return meeting_info["_documents"]

        # Strategy 2: Embedded docs from card data attribute
        embedded = meeting_info.get("embedded_docs", [])
        if embedded:
            documents = []
            for doc in embedded:
                if isinstance(doc, dict):
                    url = doc.get("url", doc.get("href", ""))
                    title = doc.get("title", doc.get("name", ""))
                    if url:
                        documents.append({
                            "download_url": urljoin(self.BASE_URL, url),
                            "doc_type": self._classify_caiso_doc(url, title),
                            "title": title,
                            "filename": unquote(url.split("/")[-1]),
                        })
            if documents:
                return documents

        # Strategy 3: Re-scrape the materials URL
        materials_url = meeting_info.get("materials_url")
        if materials_url:
            if "stakeholdercenter" in materials_url:
                return self._scrape_initiative_docs_for_date(
                    materials_url, meeting_info.get("meeting_date", "")
                )
            else:
                # caiso.com/meetings-events/topics/ pages have direct doc links
                return self._scrape_page_for_docs(materials_url)

        # Strategy 4: Try the detail URL
        detail_url = meeting_info.get("detail_url")
        if detail_url:
            return self._scrape_page_for_docs(detail_url)

        return []

    def _extract_docs_from_cell(self, cell):
        """
        Extract document links from a table cell on a stakeholder
        center initiative page.

        Documents are <a> tags linking to:
          - stakeholdercenter.caiso.com/InitiativeDocuments/{name}.pdf
          - caiso.com/documents/{name}.xlsx
          - YouTube links (skip these)
        """
        documents = []
        doc_extensions = [".pdf", ".xlsx", ".xls", ".pptx", ".docx"]

        for link in cell.select("a[href]"):
            href = link.get("href", "")
            link_text = link.get_text(strip=True)

            if "youtu" in href.lower() or "video" in link_text.lower()[:6]:
                continue

            full_url = href
            if not full_url.startswith("http"):
                full_url = urljoin(self.STAKEHOLDER_URL, href)

            is_doc = any(ext in href.lower() for ext in doc_extensions)
            is_initiative_doc = "InitiativeDocuments" in href
            is_caiso_doc = "caiso.com/documents" in href

            if not (is_doc or is_initiative_doc or is_caiso_doc):
                continue

            filename = unquote(full_url.split("/")[-1])
            title = link_text or filename

            documents.append({
                "download_url": full_url,
                "doc_type": self._classify_caiso_doc(filename, title),
                "title": title,
                "filename": filename,
            })

        return documents

    def _scrape_initiative_docs_for_date(self, initiative_url, target_date):
        """
        Scrape an initiative page for docs associated with target_date.

        The activity-date-time div is populated via JS (not static HTML),
        so we match rows by looking for the target date in document filenames
        and titles (CAISO names files like "Agenda-...-Apr-09-2026.pdf").

        Falls back to returning all docs whose post-date is within 7 days
        of target_date if no filename date match is found.
        """
        try:
            self._polite_delay()
            resp = self.session.get(initiative_url, timeout=20)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "lxml")
            phase_table = soup.select_one("table.table-phase")
            if not phase_table:
                return []

            # Build date variants to match against filenames/titles
            # e.g. target_date="2026-04-09" → "Apr-09-2026", "Apr 09, 2026"
            from datetime import datetime as _dt
            td = _dt.strptime(target_date, "%Y-%m-%d")
            date_variants = [
                td.strftime("%b-%d-%Y"),                    # Apr-09-2026
                td.strftime("%b-%d-%y"),                    # Apr-09-26
                td.strftime("%b %d, %Y"),                   # Apr 09, 2026
                f"{td.month}-{td.day}-{td.year}",           # 4-9-2026
                f"{td.month:02d}-{td.day:02d}-{td.year}",  # 04-09-2026
                target_date,                                # 2026-04-09
            ]

            matched_docs = []
            all_docs = []

            for row in phase_table.select("tr"):
                cells = row.select("td")
                if len(cells) < 2:
                    continue
                docs_cell = cells[1] if len(cells) > 1 else None
                if not docs_cell:
                    continue

                row_docs = self._extract_docs_from_cell(docs_cell)
                all_docs.extend(row_docs)

                # Check if any doc in this row references the target date
                for doc in row_docs:
                    combined = f"{doc.get('filename','')} {doc.get('title','')}".lower()
                    if any(v.lower() in combined for v in date_variants):
                        if doc not in matched_docs:
                            matched_docs.append(doc)

            if matched_docs:
                return matched_docs

            # Fallback: return docs whose post-date (from span.doc-post-date)
            # is within 7 days of the meeting date
            from datetime import datetime as _dt, timedelta
            try:
                td = _dt.strptime(target_date, "%Y-%m-%d")
                window_start = (td - timedelta(days=7)).strftime("%Y-%m-%d")
                window_end = (td + timedelta(days=1)).strftime("%Y-%m-%d")
            except Exception:
                return all_docs[:20]

            windowed = []
            for row in phase_table.select("tr"):
                cells = row.select("td")
                if len(cells) < 2:
                    continue
                docs_cell = cells[1]
                for post_span in docs_cell.select("span.doc-post-date"):
                    post_date = self._extract_date_from_text(
                        post_span.get_text(strip=True)
                    )
                    if post_date and window_start <= post_date <= window_end:
                        row_docs = self._extract_docs_from_cell(docs_cell)
                        for d in row_docs:
                            if d not in windowed:
                                windowed.append(d)
                        break  # one match per row is enough

            return windowed

        except Exception as e:
            print(f"    Error re-scraping initiative page: {e}")

        return []

    def _scrape_page_for_docs(self, page_url):
        """Generic fallback: scrape any page for document links."""
        documents = []
        doc_extensions = [".pdf", ".xlsx", ".xls", ".pptx", ".docx"]

        try:
            self._polite_delay()
            resp = self.session.get(page_url, timeout=15)
            if resp.status_code != 200:
                return documents

            soup = BeautifulSoup(resp.text, "lxml")

            for link in soup.select("a[href]"):
                href = link.get("href", "")
                full_url = urljoin(self.BASE_URL, href)

                is_doc = any(ext in href.lower() for ext in doc_extensions)
                if not is_doc:
                    continue

                parsed = urlparse(full_url)
                if "caiso.com" not in parsed.netloc:
                    continue

                filename = unquote(full_url.split("/")[-1])
                title = link.get_text(strip=True) or filename

                documents.append({
                    "download_url": full_url,
                    "doc_type": self._classify_caiso_doc(filename, title),
                    "title": title,
                    "filename": filename,
                })

        except Exception as e:
            print(f"    Error scraping page for docs: {e}")

        return documents

    # ── CAISO-specific helpers ──────────────────────────────────

    def _classify_caiso_doc(self, filename, title=""):
        """Classify a CAISO document type."""
        text = f"{filename} {title}".lower()

        if "agenda" in text:
            return "agenda"
        elif any(w in text for w in ["minutes", "draft-minutes"]):
            return "minutes"
        elif any(w in text for w in ["presentation", "slide"]):
            return "presentation"
        elif any(w in text for w in ["proposal", "straw", "issue-paper",
                                      "issue paper"]):
            return "proposal"
        elif any(w in text for w in ["comment", "public-comment"]):
            return "comment"
        elif any(w in text for w in ["tariff", "amendment"]):
            return "tariff"
        elif any(w in text for w in ["report", "summary"]):
            return "report"
        elif any(w in text for w in ["decision", "memo"]):
            return "decision"
        elif "workshop" in text:
            return "workshop-paper"
        elif any(w in text for w in ["implementation", "milestone"]):
            return "implementation"
        else:
            return "other"

    def _extract_date_from_text(self, text):
        """Extract a date from free text, return YYYY-MM-DD or None."""
        if not text:
            return None

        # MM/DD/YYYY (primary CAISO format)
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if m:
            try:
                dt = datetime(int(m.group(3)), int(m.group(1)),
                              int(m.group(2)))
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # "Month DD, YYYY"
        m = re.search(
            r"(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
            text, re.IGNORECASE
        )
        if m:
            try:
                dt = datetime.strptime(
                    f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y"
                )
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # "Mon DD, YYYY" (abbreviated)
        m = re.search(
            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
            r"\s+(\d{1,2}),?\s+(\d{4})",
            text, re.IGNORECASE
        )
        if m:
            try:
                dt = datetime.strptime(
                    f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y"
                )
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # YYYY-MM-DD
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
        if m:
            return m.group(0)

        return None

    def _extract_time_from_text(self, text):
        """Extract a time string from free text."""
        if not text:
            return None
        m = re.search(
            r"(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))"
            r"(?:\s*[-\u2013]\s*\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))?"
            r"(?:\s*(?:P[DS]T|PT))?",
            text
        )
        return m.group(0).strip() if m else None


def main():
    """Run the CAISO scraper standalone."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from db.database import init_db

    init_db()

    scraper = CAISOScraper()
    scraper.run(
        lookback_days=14,
        lookahead_days=30,
        download=True,
    )


if __name__ == "__main__":
    main()
