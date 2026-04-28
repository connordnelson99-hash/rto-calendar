#!/usr/bin/env python3
"""
ISO New England (ISO-NE) Document Scraper

Unlike PJM and CAISO, ISO-NE exposes a clean public JSON API at
www.iso-ne.com/api/1/services/, so this scraper uses plain
`requests` and avoids Playwright entirely.

-- Architecture (confirmed via live probe 2026-04-24) --

Two endpoints power the whole pipeline:

1. events.json    -- calendar feed
   GET /api/1/services/events.json
       ?sortBy=event_start_date_gmt+asc
       &fromDate=YYYY-MM-DDTHH:MM:SS
       &toDate=YYYY-MM-DDTHH:MM:SS
       &count=1000
   Returns {events: [...], facets: {...}}.

2. documents.json -- per-committee document library
   GET /api/1/services/documents.json
       ?type=doc&type=ceii&crafterSite=iso-ne
       &searchable=true&q=*&source=docLibraryWidget
       &pre_document_committee_value={Committee Name}
       &start=0&rows=200
       &sort=publish_date_dt+desc
   Each document has events_o[].item.key, which is the event_id of
   the meeting the document belongs to. We bucket docs to meetings
   on the client side, so we make ONE documents.json call per
   committee rather than per event.

The site publishes ~361 calendar entries in a typical 2-month
window, but most are auto-generated "Morning Report" notices.
We filter to real committee meetings via event_type.name and a
non-null committee_name.
"""

import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote, urljoin

from .base_scraper import BaseRTOScraper


class ISONEScraper(BaseRTOScraper):

    rto_name = "ISO-NE"

    BASE_URL = "https://www.iso-ne.com"
    CALENDAR_URL = "https://www.iso-ne.com/calendar"
    EVENTS_API = "https://www.iso-ne.com/api/1/services/events.json"
    DOCS_API = "https://www.iso-ne.com/api/1/services/documents.json"

    # event_type.name values we accept. Everything else (Notices,
    # Holiday, etc.) is filtered out. "Training Events" are skipped by
    # default -- they're multi-day classroom courses, not stakeholder
    # meetings. Override at instance level if you want them.
    KEEP_EVENT_TYPES = {"Meetings"}

    # The events.json payload always has committee_name == null. The
    # actual committee lives in event_subtypes[0].name as the literal
    # string " Name:<Committee>" (with the leading space and 'Name:'
    # prefix coming from a server-side serialization quirk). When the
    # subtype is this catch-all bucket, fall back to parsing the title.
    SUBTYPE_CATCHALL = "Other Committees and Working Groups"

    # Cap on documents.json rows per committee. ISO-NE returns up to
    # 2000+ for big committees but we only need recent ones.
    DOCS_PER_COMMITTEE = 200

    def __init__(self):
        super().__init__()
        # Cache: committee_name -> list of document dicts (from API).
        # Lets scrape_meeting_documents() share one API call across
        # every event in the same committee.
        self._docs_by_committee = {}

    # -- Phase 1: meetings --------------------------------------------

    def scrape_meetings(self, lookback_days=14, lookahead_days=30):
        start, end = self._date_range(lookback_days, lookahead_days)
        from_dt = f"{start}T00:00:00"
        to_dt = f"{end}T23:59:59"

        # NOTE: ISO-NE's events.json rejects URL-encoded ':' and '+'.
        # We build the query string by hand so requests doesn't encode
        # them. (Tested 2026-04-24: encoded -> 0 events; literal -> 194.)
        query = (
            f"sortBy=event_start_date_gmt+asc"
            f"&fromDate={from_dt}"
            f"&toDate={to_dt}"
            f"&count=1000"
        )
        url = f"{self.EVENTS_API}?{query}"

        print(f"  Fetching events {start} -> {end} ...")
        self._polite_delay()
        resp = self.session.get(url, timeout=30,
                                headers={"Accept": "application/json"})
        resp.raise_for_status()
        payload = resp.json()
        raw_events = payload.get("events", [])
        print(f"  events.json returned {len(raw_events)} raw items")

        meetings = []
        for ev in raw_events:
            if ev.get("cancelled_flag") == "Y":
                continue
            if ev.get("deleted_flag") == "Y":
                continue

            event_type = (ev.get("event_type") or {}).get("name")
            if event_type not in self.KEEP_EVENT_TYPES:
                continue

            title = ev.get("event_title") or "(untitled)"
            committee = self._parse_committee(ev, title)
            if not committee:
                continue
            start_str = ev.get("event_start_date_gmt_str")  # "2026-04-09T13:00:00"
            if not start_str:
                continue

            try:
                dt = datetime.fromisoformat(start_str)
            except ValueError:
                continue

            meeting_date = dt.strftime("%Y-%m-%d")
            # Portable 12-hour format: '%-I' is GNU-only, '%#I' is
            # Windows-only, so use '%I' + lstrip('0') for both.
            meeting_time = dt.strftime("%I:%M %p").lstrip("0")

            event_id = str(ev.get("event_id") or "").strip()
            if not event_id:
                continue

            meetings.append({
                "event_id": event_id,
                "title": title.strip(),
                "meeting_date": meeting_date,
                "meeting_time": meeting_time,
                "committee": committee.strip(),
                "location": (ev.get("location") or "").strip() or None,
                "source_url": self.CALENDAR_URL,
                "detail_url": (
                    f"{self.BASE_URL}/calendar?eventId={event_id}"
                ),
                "materials_url": ev.get("committee_link") or None,
            })

        print(f"  {len(meetings)} meetings kept after filtering")
        return meetings

    # -- Phase 2: documents -------------------------------------------

    def scrape_meeting_documents(self, meeting_info):
        committee = meeting_info.get("committee")
        event_id = meeting_info.get("event_id")
        if not committee or not event_id:
            return []

        docs = self._fetch_committee_docs(committee)

        matched = []
        for doc in docs:
            event_keys = self._extract_event_keys(doc)
            if event_id not in event_keys:
                continue

            path = doc.get("path") or (
                (doc.get("file_o") or {}).get("item", {}).get("value")
            )
            if not path:
                continue
            url = urljoin(self.BASE_URL, path)

            title = (doc.get("document_title_s")
                     or doc.get("normalized_document_title_s")
                     or path.rsplit("/", 1)[-1])

            doc_type_label = self._first_value_smv(doc.get("document_type_o"))

            posted = doc.get("publish_date_dt")
            if posted and "T" in posted:
                posted = posted.split("T", 1)[0]

            matched.append({
                "download_url": url,
                "doc_type": (
                    self._classify_doc(title) if not doc_type_label
                    else self._normalize_doc_type(doc_type_label, title)
                ),
                "title": title,
                "filename": path.rsplit("/", 1)[-1],
                "posted_date": posted,
            })

        return matched

    # -- Helpers ------------------------------------------------------

    def _parse_committee(self, ev, title):
        """
        Resolve the committee for an event. Two-tier strategy:

        1. event_subtypes[0].name -- the API serializes this as
           " Name:Markets Committee" (note the leading space and
           "Name:" prefix). We strip both. If this resolves to the
           generic "Other Committees and Working Groups" bucket, fall
           through to step 2.

        2. Title-based extraction. ISO-NE meeting titles follow
           "NEPOOL <Committee Name> Meeting" or "<Name> Meeting".
           Strip leading "NEPOOL " and trailing " Meeting".
        """
        subtypes = ev.get("event_subtypes") or []
        sub_name = ""
        if subtypes:
            sub_name = (subtypes[0].get("name") or "").strip()
            # Pattern: " Name:Markets Committee]" or "Name:Markets Committee"
            sub_name = sub_name.lstrip("[").rstrip("]")
            sub_name = re.sub(r"^\s*Name\s*:\s*", "", sub_name).strip()

        if sub_name and sub_name != self.SUBTYPE_CATCHALL:
            return sub_name

        # Fall through to title parsing.
        cleaned = title
        cleaned = re.sub(r"^NEPOOL\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+Meeting\b.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned)
        return cleaned.strip() or None

    def _fetch_committee_docs(self, committee):
        """One documents.json call per committee, cached on self."""
        if committee in self._docs_by_committee:
            return self._docs_by_committee[committee]

        # Same encoding caveat as events.json: literal '+' and ':' only,
        # but the committee name itself does need URL-encoding (spaces).
        committee_q = quote(committee, safe="")
        query = (
            "type=doc&type=ceii"
            "&crafterSite=iso-ne"
            "&searchable=true&includeVersions=false"
            "&q=*&source=docLibraryWidget"
            f"&pre_document_committee_value={committee_q}"
            f"&start=0&rows={self.DOCS_PER_COMMITTEE}"
            "&sort=publish_date_dt+desc"
        )
        url = f"{self.DOCS_API}?{query}"

        print(f"    Fetching docs for committee: {committee}")
        try:
            self._polite_delay()
            r = self.session.get(url, timeout=30,
                                 headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
            docs = data.get("documents", []) or []
            total = data.get("total", len(docs))
            if total > len(docs):
                print(f"      (got {len(docs)} of {total} -- bump "
                      f"DOCS_PER_COMMITTEE if older meetings need coverage)")
            self._docs_by_committee[committee] = docs
            return docs
        except Exception as e:
            print(f"      docs.json fetch failed for {committee}: {e}")
            self._docs_by_committee[committee] = []
            return []

    def _extract_event_keys(self, doc):
        """
        Pull the set of event_id strings a document is linked to.

        Crafter CMS serializes events_o as:
            [{"item": {"key": "...", "value_smv": "..."}}]   # 1 event
        OR
            [{"item": [{"key": "..."}, {"key": "..."}]}]     # N events

        We handle both. Empty list is normal (doc not tied to any
        meeting -- e.g. NEPOOL bylaws).
        """
        keys = set()
        for wrapper in (doc.get("events_o") or []):
            if not isinstance(wrapper, dict):
                continue
            item = wrapper.get("item")
            if isinstance(item, dict):
                k = str(item.get("key", "")).strip()
                if k:
                    keys.add(k)
            elif isinstance(item, list):
                for entry in item:
                    if isinstance(entry, dict):
                        k = str(entry.get("key", "")).strip()
                        if k:
                            keys.add(k)
        return keys

    def _first_value_smv(self, field):
        """
        Pull the first 'value_smv' from a Crafter '_o' multi-value
        field. Same shape variance as events_o: item can be a dict
        or a list. Returns '' if nothing found.
        """
        for wrapper in (field or []):
            if not isinstance(wrapper, dict):
                continue
            item = wrapper.get("item")
            if isinstance(item, dict):
                v = item.get("value_smv")
                if v:
                    return v
            elif isinstance(item, list):
                for entry in item:
                    if isinstance(entry, dict):
                        v = entry.get("value_smv")
                        if v:
                            return v
        return ""

    def _normalize_doc_type(self, label, title):
        """Map ISO-NE's 'document_type' label onto our shared taxonomy."""
        lab = (label or "").lower()
        if "agenda" in lab:
            return "agenda"
        if "minutes" in lab:
            return "minutes"
        if "presentation" in lab or "slide" in lab:
            return "presentation"
        if "notice" in lab:
            return "notice"
        if "material" in lab:
            # Generic "Meeting Materials" -- inspect the title for a
            # finer-grained tag, fall back to base classifier.
            return self._classify_doc(title)
        if "report" in lab:
            return "report"
        return self._classify_doc(title) or "other"
