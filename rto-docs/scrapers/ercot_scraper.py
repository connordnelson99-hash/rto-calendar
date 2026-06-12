#!/usr/bin/env python3
"""
ERCOT Document Scraper

Fully requests-only — no Playwright. ERCOT's meeting calendar is a
server-rendered Apache/jQuery site (no JSON API, but none needed).

── Architecture (confirmed via live probe 2026-06-12) ──────────────

1. Meetings — GET https://www.ercot.com/calendar?fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD
   returns the ENTIRE window as one server-rendered page (no pagination;
   past meetings included; min date on the picker is 2002). Events are
   grouped under date-header divs (class "subtitle1", e.g.
   "Monday, Jun 15, 2026"); each row carries:
     - a checkbox whose value is the meeting GUID
     - <a href=".../calendar/<MMDDYYYY>-<slug>" title="<full committee name>">
       with the short meeting title as link text
     - a plain <span> location ("Webex", "Met Center", ...)
     - <span class="startTime">9:30 AM</span> — Central wall clock
       (webcal-v2 already maps ERCOT -> America/Chicago)
   A per-meeting iCal also exists at /ical/meetings?id=<guid> (with full
   VTIMEZONE) but isn't needed — the list gives us date + start time.

2. Documents — each detail page has a "Key Documents" section of direct
   per-file links (<a download href="https://www.ercot.com/files/docs/
   YYYY/MM/DD/<file>">), mostly .docx/.pptx/.doc/.xls — almost no PDFs,
   so this scraper extracts .docx/.pptx text locally (see _extract_text).
   Legacy binary .doc/.xls are downloaded but not text-extracted; they
   still get title-only screening.

   Zip bundles ("Meeting Materials ...zip", "Revision Requests ...zip")
   are DELIBERATELY SKIPPED: probed live 2026-06-12, they re-post the
   entire historical filing trail of every open revision request at
   every committee stop (a PRS bundle held 160 entries dating to 2024,
   all DEFLATE so no SPP-style range tricks). Ingesting them would
   attach ~100 mostly-stale docs per WMS/PRS/TAC meeting and re-screen
   the same content at each committee. The loose Key Documents are what
   presenters actually bring to THIS meeting. The fresh per-NPRR filings
   live on ERCOT's structured issue pages (/mktrules/issues/NPRR####) —
   that's future issues-scraper territory, not meeting materials.

3. Agendas — detail pages embed a rich HTML agenda table (topics with
   NPRR numbers + presenters). ERCOT posts the agenda file as legacy
   .doc (unextractable), so the inline HTML is the best text source:
   it's captured as a synthetic "agenda" document (download_url =
   <detail_url>#agenda) whose text is written to a local .txt and saved
   as extracted_text — giving Stage-2 screening the full topic list.

Meeting titles are just "<acronym> Meeting" with no agenda signal, so
like NYISO/SPP/MISO the Stage-1 meeting gate is bypassed for ERCOT in
screen_documents.py — every doc is screened on its own extracted text.

Rows with an empty committee (title attribute) are skipped — those are
holiday/notice entries, not meetings. Rows mentioning "cancel" are
skipped defensively (none were live during the probe; the site's status
facet implies Cancelled rows do appear inline).
"""

import re
import zipfile
from datetime import datetime
from html import unescape
from urllib.parse import urlparse, unquote

from .base_scraper import BaseRTOScraper

_DATE_HEADER_RE = re.compile(
    r'class="subtitle1[^"]*"[^>]*>\s*([^<]+?)\s*</div>')
_EVENT_ANCHOR_RE = re.compile(
    r'<a\s+href="(https://www\.ercot\.com/calendar/[^"]+)"\s+'
    r'title="([^"]*)"[^>]*>\s*([^<]*?)\s*</a>')
_START_TIME_RE = re.compile(r'<span class="startTime">\s*([^<]*?)\s*</span>')
_PLAIN_SPAN_RE = re.compile(r'<span>\s*([^<]+?)\s*</span>')
_KEY_DOC_RE = re.compile(
    r'<a\s+download\s+href="([^"]+)"\s+title="([^"]*)"')
_FILES_DATE_RE = re.compile(r'/files/docs/(\d{4})/(\d{2})/(\d{2})/')
_AGENDA_RE = re.compile(
    r'<h5>Agenda</h5>(.*?)(?:<section id="otherMeetings"|</div>\s*<div class="clear")',
    re.S)
_WEBEX_SUFFIX_RE = re.compile(r'\s*[-–]\s*(Webex( Only)?|Hybrid)?\s*$', re.I)


class ERCOTScraper(BaseRTOScraper):

    rto_name = "ERCOT"

    BASE_URL = "https://www.ercot.com"
    CALENDAR_URL = "https://www.ercot.com/calendar"

    SKIP_DOC_EXTS = (".zip", ".ics")

    def __init__(self):
        super().__init__()
        self._agenda_text = {}  # detail_url#agenda -> extracted agenda text

    # ── Phase 1: meetings ────────────────────────────────────────

    def scrape_meetings(self, lookback_days=14, lookahead_days=30):
        start_date, end_date = self._date_range(lookback_days, lookahead_days)
        print(f"  Scraping ERCOT meeting calendar ({start_date} to {end_date})")

        self._polite_delay()
        resp = self.session.get(
            self.CALENDAR_URL,
            params={"fromDate": start_date, "toDate": end_date},
            timeout=30,
        )
        resp.raise_for_status()
        html = resp.text

        # Split the page into per-day segments at the date headers, then
        # parse the event rows inside each segment.
        headers = [(m.start(), m.group(1)) for m in
                   _DATE_HEADER_RE.finditer(html)]
        meetings, seen = [], set()
        for i, (pos, header_text) in enumerate(headers):
            date = self._parse_header_date(header_text)
            if not date:
                continue
            seg_end = headers[i + 1][0] if i + 1 < len(headers) else len(html)
            segment = html[pos:seg_end]

            anchors = list(_EVENT_ANCHOR_RE.finditer(segment))
            for j, a in enumerate(anchors):
                detail_url, committee, title = (
                    a.group(1), unescape(a.group(2)).strip(),
                    unescape(a.group(3)).strip())
                if not committee:
                    continue  # holiday / notice rows have no committee
                row_end = (anchors[j + 1].start()
                           if j + 1 < len(anchors) else len(segment))
                row = segment[a.end():row_end]
                if "cancel" in title.lower() or "cancel" in row[:400].lower():
                    continue
                if detail_url in seen:
                    continue
                seen.add(detail_url)

                meetings.append({
                    "title": self._clean_title(title),
                    "meeting_date": date,
                    "meeting_time": self._start_time(row),
                    "committee": committee,
                    "location": self._location(row),
                    "source_url": self.CALENDAR_URL,
                    "detail_url": detail_url,
                    "materials_url": detail_url,
                })

        print(f"  {len(meetings)} ERCOT meetings in window")
        return meetings

    @staticmethod
    def _parse_header_date(text):
        """'Monday, Jun 15, 2026' -> '2026-06-15'."""
        try:
            return datetime.strptime(
                text.strip(), "%A, %b %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def _clean_title(title):
        """Drop venue suffixes like 'IBRWG Meeting - Webex Only'."""
        return _WEBEX_SUFFIX_RE.sub("", title).strip() or title

    @staticmethod
    def _start_time(row):
        m = _START_TIME_RE.search(row)
        return m.group(1) if m and m.group(1) else None

    @staticmethod
    def _location(row):
        """Classless <span>s before the 'Add to' iCal link are location."""
        cut = row.find("/ical/")
        scope = row[:cut] if cut > 0 else row
        spots = [unescape(s) for s in _PLAIN_SPAN_RE.findall(scope)
                 if unescape(s).strip() not in ("&", "&amp;", "and")]
        return " & ".join(spots) or None

    # ── Phase 2: documents ───────────────────────────────────────

    def scrape_meeting_documents(self, meeting_info):
        detail_url = meeting_info.get("detail_url")
        if not detail_url:
            return []

        try:
            self._polite_delay()
            resp = self.session.get(detail_url, timeout=30)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            print(f"    [{detail_url}] detail fetch failed: {e}")
            return []

        documents = []

        # Synthetic agenda doc from the inline HTML agenda (the posted
        # agenda file is legacy .doc, which we can't extract).
        agenda = self._agenda_from(html)
        if agenda and len(agenda) >= 40:
            agenda_url = detail_url + "#agenda"
            self._agenda_text[agenda_url] = agenda
            documents.append({
                "download_url": agenda_url,
                "doc_type": "agenda",
                "title": (f"{meeting_info['title']} Agenda "
                          f"{meeting_info['meeting_date']}"),
                "filename": None,
                "posted_date": None,
            })

        # Key Documents: direct per-file links; skip zip bundles (see
        # module docstring) and iCal links.
        seen = set()
        for m in _KEY_DOC_RE.finditer(html):
            url, title = m.group(1), unescape(m.group(2)).strip()
            if url in seen:
                continue
            seen.add(url)
            path = urlparse(url).path.lower()
            if path.endswith(self.SKIP_DOC_EXTS):
                continue
            dm = _FILES_DATE_RE.search(url)
            posted = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}" if dm else None
            filename = unquote(urlparse(url).path.split("/")[-1])
            documents.append({
                "download_url": url,
                "doc_type": self._classify_doc(title or filename),
                "title": title or filename,
                "filename": filename,
                "posted_date": posted,
            })

        return documents

    @staticmethod
    def _agenda_from(html):
        """Strip the inline HTML agenda (pre-agenda + agenda table +
        post-agenda) down to plain text."""
        m = _AGENDA_RE.search(html)
        if not m:
            return None
        block = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", m.group(1),
                       flags=re.S)
        block = re.sub(r"</(p|tr|li|div|h\d)>|<br\s*/?>", "\n", block)
        block = re.sub(r"</td>", "  ", block)
        text = unescape(re.sub(r"<[^>]+>", "", block))
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\s*\n\s*", "\n", text).strip()
        return re.sub(r"\n{2,}", "\n", text) or None

    # ── Download / extraction overrides ──────────────────────────

    def _download_document(self, url, committee, meeting_date):
        """Synthetic #agenda docs are written from the cached inline-HTML
        text instead of fetched; everything else uses the base path."""
        if "#agenda" not in url:
            return super()._download_document(url, committee, meeting_date)

        slug = url.split("/calendar/")[-1].split("#")[0]
        local_dir = (self.DOCS_DIR / self.rto_name.lower()
                     / self._sanitize_committee_slug(committee)
                     / meeting_date[:7])
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / f"{self._sanitize_committee_slug(slug)}-agenda.txt"

        text = self._agenda_text.get(url)
        if local_path.exists() and text is None:
            return str(local_path), None  # text already in DB
        if text is None:
            return None, None
        local_path.write_text(text, encoding="utf-8")
        print(f"      Captured inline agenda ({len(text):,} chars)")
        return str(local_path), text

    def _extract_text(self, local_path):
        """ERCOT posts almost no PDFs — add local .docx/.pptx extraction
        on top of the base (pdf/txt/csv) support. Legacy .doc/.xls are
        left unextracted (title-only screening)."""
        from pathlib import Path
        suffix = Path(local_path).suffix.lower()
        if suffix == ".docx":
            return self._docx_text(local_path)
        if suffix in (".pptx", ".potx"):
            return self._pptx_text(local_path)
        return super()._extract_text(local_path)

    @staticmethod
    def _docx_text(local_path):
        try:
            with zipfile.ZipFile(local_path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
        except Exception:
            return None
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<[^>]+>", "", xml)
        return re.sub(r"\n{3,}", "\n\n", unescape(xml)).strip() or None

    @staticmethod
    def _pptx_text(local_path):
        """Slide TEXT only — text baked into images/charts is not OCR'd."""
        try:
            with zipfile.ZipFile(local_path) as z:
                slides = sorted(
                    (n for n in z.namelist()
                     if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                    key=lambda n: int(re.search(r"slide(\d+)", n).group(1)))
                parts = []
                for name in slides[:80]:
                    xml = z.read(name).decode("utf-8", "replace")
                    runs = re.findall(r"<a:t>([^<]*)</a:t>", xml)
                    if runs:
                        parts.append(unescape(" ".join(runs)))
        except Exception:
            return None
        return "\n\n".join(parts).strip() or None


def main():
    """Run the ERCOT scraper standalone."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from db.database import init_db

    init_db()
    ERCOTScraper().run(lookback_days=14, lookahead_days=30, download=True)


if __name__ == "__main__":
    main()
