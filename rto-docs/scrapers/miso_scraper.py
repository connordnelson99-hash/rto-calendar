#!/usr/bin/env python3
"""
MISO (Midcontinent ISO) Document Scraper

Fully requests-only — no Playwright. MISO's site is Optimizely (Episerver)
with two public JSON APIs that cover everything we need.

── Architecture (confirmed via live probe 2026-06-10) ──────────────

1. Meetings — the stakeholder calendar at /engage/tools/calendar/ is a
   React/Kendo scheduler reading:

       GET /api/events/geteventsformonth?month=M&year=Y

   Each event carries name, meetingDate, startDate/endDate (UTC with real
   clock times), eventCanceled, hostLocation, linkURL (detail page), and
   entityReferenceList — the committee(s), each with a url of the form
   /link/<32-hex-guid>.aspx. That guid IS the committee page guid the
   materials search keys on, so no detail-page fetch is needed.

   The response covers the calendar GRID for that month (it spills a few
   days into neighbouring months), so a window spanning a month boundary
   gets events twice — dedupe by contentGuid.

2. Materials — event pages embed a RelatedMeetingMaterialDocuments React
   component that POSTs an Elasticsearch-style query to:

       POST /api/find/Optics_Models_Find_RemoteHostedContentItem/_search

   filtered on RelatedPages=<committee page guid>, committeedoctype in
   ("Meeting Material", "Both"), and a one-day Properties.meetingdate
   range. No auth, no CSRF token. Hits carry Name, FileName, ObjectId,
   SearchFileExtension, SearchPublishDate, plus screening-friendly
   metadata (topicaslist, issueid1-3, entityname).

3. Downloads — the site's /api/documents/getbyname|getbymediaId routes
   500 for anonymous callers, but every document is public on the CDN:

       https://cdn.misoenergy.org/{FileName minus extension}{ObjectId}.{ext}

   (URL-encoded; supports HTTP Range; serves proper content types.)
   The key must come from FileName, not Name — Name is a display title
   that sometimes differs from the stored file name (88/88 in-window
   docs resolved with the FileName stem on 2026-06-10; Name-based keys
   403 whenever the two diverge).

Meeting titles are just "<Committee> - <date>" with no agenda signal, so
like NYISO/SPP the Stage-1 meeting gate is bypassed for MISO in
screen_documents.py — every doc is screened on its own extracted text.

Times: the events API returns UTC; webcal-v2 tags MISO as Central
(RTO_SOURCE_TZ), so meeting_time is emitted as CT wall clock.
"""

import re
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .base_scraper import BaseRTOScraper

_LINK_GUID_RE = re.compile(r"/link/([0-9a-fA-F]{32})\.aspx")
_CENTRAL = ZoneInfo("America/Chicago")


class MISOScraper(BaseRTOScraper):

    rto_name = "MISO"

    BASE_URL = "https://www.misoenergy.org"
    CALENDAR_URL = "https://www.misoenergy.org/engage/tools/calendar/"
    EVENTS_API = BASE_URL + "/api/events/geteventsformonth"
    FIND_API = (BASE_URL
                + "/api/find/Optics_Models_Find_RemoteHostedContentItem"
                + "/_search")
    CDN_BASE = "https://cdn.misoenergy.org"

    # ── Phase 1: meetings ────────────────────────────────────────

    def scrape_meetings(self, lookback_days=14, lookahead_days=30):
        start_date, end_date = self._date_range(lookback_days, lookahead_days)
        print(f"  Scraping MISO event calendar ({start_date} to {end_date})")

        events = {}
        for year, month in self._months_in_window(start_date, end_date):
            for e in self._fetch_month(year, month):
                guid = e.get("contentGuid") or e.get("linkURL")
                if guid:
                    events[guid] = e

        meetings = []
        for e in events.values():
            if e.get("eventCanceled"):
                continue
            date = (e.get("meetingDate") or "")[:10]
            if not date or not (start_date <= date <= end_date):
                continue

            committees = self._committees(e)
            link = e.get("linkURL") or ""
            detail_url = self.BASE_URL + link if link.startswith("/") else link
            meetings.append({
                "title": e.get("name") or "MISO meeting",
                "meeting_date": date,
                "meeting_time": self._central_time_range(e),
                "committee": committees[0][0] if committees else None,
                "location": e.get("hostLocation") or None,
                "source_url": self.CALENDAR_URL,
                "detail_url": detail_url or None,
                "materials_url": detail_url or None,
                # carried to phase 2:
                "_committee_guids": [g for _, g in committees],
            })

        print(f"  {len(meetings)} MISO meetings in window")
        return meetings

    def _fetch_month(self, year, month):
        try:
            self._polite_delay()
            resp = self.session.get(
                self.EVENTS_API, params={"month": month, "year": year},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("events") or []
        except Exception as e:
            print(f"    [{year}-{month:02d}] events fetch failed: {e}")
            return []

    @staticmethod
    def _months_in_window(start_date, end_date):
        """Yield (year, month) for every month the window touches."""
        y, m = int(start_date[:4]), int(start_date[5:7])
        end_y, end_m = int(end_date[:4]), int(end_date[5:7])
        while (y, m) <= (end_y, end_m):
            yield y, m
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    @staticmethod
    def _committees(event):
        """[(name, hyphenated-guid), ...] from entityReferenceList."""
        out = []
        for ref in event.get("entityReferenceList") or []:
            m = _LINK_GUID_RE.search(ref.get("url") or "")
            if not m:
                continue
            h = m.group(1).lower()
            guid = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
            out.append(((ref.get("text") or "").strip() or None, guid))
        return out

    @staticmethod
    def _central_time_range(event):
        """startDate/endDate (UTC) → '9:00 AM - 11:00 AM' CT, or None."""
        def to_ct(iso):
            try:
                return datetime.fromisoformat(iso).astimezone(_CENTRAL)
            except (TypeError, ValueError):
                return None

        def clock(dt):
            ap = "AM" if dt.hour < 12 else "PM"
            return f"{dt.hour % 12 or 12}:{dt.minute:02d} {ap}"

        start = to_ct(event.get("startDate"))
        end = to_ct(event.get("endDate"))
        if not start or (start.hour, start.minute) == (0, 0):
            return None  # all-day / no real clock time
        if end and end > start:
            return f"{clock(start)} - {clock(end)}"
        return clock(start)

    # ── Phase 2: documents ───────────────────────────────────────

    def scrape_meeting_documents(self, meeting_info):
        guids = meeting_info.get("_committee_guids") or []
        date = meeting_info["meeting_date"]

        documents, seen = [], set()
        for guid in guids:
            for hit in self._find_materials(guid, date):
                src = hit.get("_source") or {}
                name = src.get("Name") or src.get("Name$$string")
                filename = src.get("FileName") or src.get("FileName$$string")
                obj_id = src.get("ObjectId$$number")
                ext = (src.get("SearchFileExtension$$string") or "").lstrip(".")
                if not filename or obj_id is None or not ext:
                    continue
                if obj_id in seen:
                    continue  # joint meetings share materials across guids
                seen.add(obj_id)

                stem = (filename[: -(len(ext) + 1)]
                        if filename.lower().endswith("." + ext.lower())
                        else filename)
                posted = (src.get("SearchPublishDate$$date")
                          or src.get("Created$$date") or "")[:10]
                documents.append({
                    "download_url": (
                        f"{self.CDN_BASE}/{quote(stem)}{obj_id}.{ext}"
                    ),
                    "doc_type": self._classify_doc(name or filename),
                    "title": name or stem,
                    "filename": filename,
                    "posted_date": posted or None,
                })
        return documents

    def _find_materials(self, committee_guid, date):
        """Query the find service for one committee guid + meeting day."""
        next_day = (
            datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        query = {
            "from": 0,
            "size": 200,
            "sort": [{"Updated": "desc"}, {"Name": "asc"}],
            "query": {"filtered": {"filter": {"and": [
                {"query": {"term": {"RelatedPages": committee_guid}}},
                {"or": [
                    {"query": {"term":
                        {"Properties.committeedoctype": "Meeting Material"}}},
                    {"query": {"term":
                        {"Properties.committeedoctype": "Both"}}},
                ]},
                {"exists": {"field": "Properties.meetingdate"}},
                {"range": {"Properties.meetingdate": {
                    "gte": f"{date}T00:00:00",
                    "lt": f"{next_day}T00:00:00",
                }}},
            ]}}},
        }
        try:
            self._polite_delay()
            resp = self.session.post(
                self.FIND_API, json=query, timeout=30,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return (resp.json().get("hits") or {}).get("hits") or []
        except Exception as e:
            print(f"    [find {committee_guid[:8]} {date}] failed: {e}")
            return []


def main():
    """Run the MISO scraper standalone."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from db.database import init_db

    init_db()
    MISOScraper().run(lookback_days=14, lookahead_days=30, download=True)


if __name__ == "__main__":
    main()
