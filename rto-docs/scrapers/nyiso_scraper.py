#!/usr/bin/env python3
"""
NYISO (New York ISO) Document Scraper

Like ISO-NE, NYISO is scrapeable with plain `requests` — no Playwright.
The site is Liferay 7.4, and every committee/working-group page embeds a
React "Committee File Browser" backed by a public REST API under
/o/committeefile/. The API needs no login — just the session cookies and
the page's Liferay CSRF token (guest auth token).

── Architecture (confirmed via live probe + Playwright capture 2026-06-05) ──

Each committee page (e.g. /business-issues-committee-bic) carries, inline
in its HTML:
  • Liferay.authToken  →  the CSRF token  (sent as x-csrf-token / p_auth)
  • plid=<number>      →  the page layout id
  • portletId: '<com_liferay_..._committee_file_browser_INSTANCE_xxxx>'
    The API expects this value PREFIXED with "portlet_".

Three POST endpoints (JSON body, x-csrf-token header, session cookies):

  1. /o/committeefile/meetingsbydate   {plid, portletId, meetingDate}
       meetingDate=null  → bootstrap. Returns config:
         { rootFolderPath, selectedYear,
           years:    "<json {data:[{year,id}]}>",   # year → year-folder id
           meetings: "<json {data:[{date,id}]}>" }   # default-year meetings
  2. /o/committeefile/meetings          {plid, portletId, folderId}
       folderId = a YEAR-folder id (from `years`). Returns
         meetings: [ {date:"YYYY-MM-DD", id:<meetingFolderId>, open}, ... ]
  3. /o/committeefile/files             {plid, portletId, folderId}
       folderId = a MEETING-folder id. Returns
         files: [ {date:"YYYY/MM/DD", name, fileUrl, id, fileType}, ... ]

Document URLs are PUBLIC PDFs (and .zip/.pptx/.xlsx) in the Liferay doc
library:
    https://www.nyiso.com/documents/{groupId}/{folderId}/{name}/{uuid}
The trailing /{uuid} is optional — dropping it leaves a URL ending in the
real filename (with extension), which lets the base scraper name the local
file correctly and extract PDF text. We store that no-uuid form.

The "MyNYISO" login that search engines mention gates a SEPARATE member
community; committee meeting materials themselves are public.

Many pages are stale archives (their newest year folder is years old, e.g.
MIWG / PRLWG stop at 2018). Since we only query the year(s) spanning the
lookback/lookahead window, those pages simply yield nothing in-window and
cost just one bootstrap call apiece.

The committeefile API exposes only the meeting DATE per committee, not a
start time, so meeting_time is left null. (A future enrichment could match
against the public iCal feeds at /o/oasis-rest/calendar/export/{id}.ics.)
"""

import re
import json
from datetime import datetime

from .base_scraper import BaseRTOScraper

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class NYISOScraper(BaseRTOScraper):

    rto_name = "NYISO"

    BASE_URL = "https://www.nyiso.com"
    CALENDAR_URL = "https://www.nyiso.com/committees"
    API_BASE = "https://www.nyiso.com/o/committeefile"

    # Committee / working-group pages exposing the public Committee File
    # Browser API. Discovered 2026-06-05 by probing every one-segment link
    # on /committees for a <nyiso-committee-file-browser> element with a
    # working meetingsbydate response, deduped by portlet instance.
    # Regenerate with: NYISOScraper.discover_committee_pages()
    COMMITTEE_PAGES = [
        ("Business Issues Committee", "business-issues-committee-bic"),
        ("Management Committee", "management-committee-mc"),
        ("Operating Committee", "operating-committee-oc"),
        ("Installed Capacity Working Group", "icapwg"),
        ("Market Issues Working Group", "miwg"),
        ("Price-Responsive Load Working Group", "prlwg"),
        ("Electric System Planning Working Group", "espwg"),
        ("Transmission Planning Advisory Subcommittee", "tpas"),
        ("Load Forecasting Task Force", "lftf"),
        ("Billing, Accounting & Credit Policy Working Group", "bacwg"),
        ("Electric Gas Coordination Working Group", "egcwg"),
        ("Budget & Priorities Working Group", "bpwg"),
        ("Business Intelligence Task Force", "bitf"),
        ("Interconnection Issues Task Force", "iitf"),
        ("Interconnection Project Facilities Study Working Group", "ipfswg"),
        ("Inter-area Planning Stakeholder Advisory Committee", "ipsac"),
        ("Communication & Data Advisory Subcommittee", "cdas"),
        ("System Operations Advisory Subcommittee", "soas"),
        ("System Protection Advisory Subcommittee", "spas"),
        ("Market Participant Audit Advisory Subcommittee", "mpaas"),
        ("Liaison Subcommittee", "liaison-subcommittee"),
        ("By-Laws Subcommittee", "bylaws"),
        ("Appeals to the Board", "appeals-to-the-board"),
        ("Environmental Advisory Council", "environmental-advisory-council"),
        ("Customer Support Focus Group", "customer-support-focus-group"),
    ]

    _AUTH_TOKEN_RE = re.compile(r"authToken\s*=\s*'([^']+)'")
    _PLID_RE = re.compile(r"plid=([0-9]+)")
    _PORTLET_RE = re.compile(
        r"portletId:\s*'([^']+committee_file_browser_INSTANCE_[a-z0-9]+)'"
    )

    def __init__(self):
        super().__init__()
        # slug -> {auth, plid, portletId} captured from the committee page.
        # Shared between scrape_meetings (phase 1) and the per-meeting
        # scrape_meeting_documents (phase 2) calls within a single run.
        self._handshake = {}

    # ── Phase 1: meetings ────────────────────────────────────────

    def scrape_meetings(self, lookback_days=14, lookahead_days=30):
        start_date, end_date = self._date_range(lookback_days, lookahead_days)
        target_years = {
            str(y) for y in range(int(start_date[:4]), int(end_date[:4]) + 1)
        }
        print(f"  Scraping NYISO committee pages "
              f"({start_date} to {end_date}); years {sorted(target_years)}")

        meetings = []
        for name, slug in self.COMMITTEE_PAGES:
            try:
                meetings.extend(
                    self._scrape_committee(name, slug, start_date,
                                           end_date, target_years)
                )
            except Exception as e:
                print(f"    [{name}] error: {e}")

        print(f"  {len(meetings)} NYISO meetings in window")
        return meetings

    def _scrape_committee(self, name, slug, start_date, end_date,
                          target_years):
        hs = self._get_handshake(slug)
        if not hs:
            return []

        # Bootstrap: pull the year → year-folder-id map.
        boot = self._api(slug, "meetingsbydate", {"meetingDate": None})
        cfg = (boot or {}).get("config") or {}
        years_raw = cfg.get("years")
        if not years_raw:
            return []
        try:
            year_map = {
                str(y.get("year")): y.get("id")
                for y in json.loads(years_raw).get("data", [])
            }
        except (ValueError, TypeError):
            return []

        meetings = []
        for year in sorted(target_years & set(year_map)):
            folder_id = year_map[year]
            resp = self._api(slug, "meetings", {"folderId": folder_id})
            for item in self._meeting_items(resp):
                date = item.get("date")
                if not date or not (start_date <= date <= end_date):
                    continue
                meeting_folder = str(item.get("id") or "").strip()
                if not meeting_folder:
                    continue
                page_url = f"{self.BASE_URL}/{slug}"
                meetings.append({
                    "title": name,
                    "meeting_date": date,
                    "meeting_time": None,
                    "committee": name,
                    "source_url": self.CALENDAR_URL,
                    "detail_url": page_url,
                    "materials_url": page_url,
                    # carried to phase 2:
                    "_slug": slug,
                    "_folder_id": meeting_folder,
                })

        if meetings:
            print(f"    [{name}] {len(meetings)} meeting(s) in window")
        return meetings

    @staticmethod
    def _meeting_items(resp):
        """meetings(folderId) returns a list under 'meetings'."""
        if not isinstance(resp, dict):
            return []
        m = resp.get("meetings")
        if isinstance(m, list):
            return m
        if isinstance(m, str):
            try:
                return json.loads(m).get("data", [])
            except ValueError:
                return []
        return []

    # ── Phase 2: documents ───────────────────────────────────────

    def scrape_meeting_documents(self, meeting_info):
        slug = meeting_info.get("_slug")
        folder_id = meeting_info.get("_folder_id")
        if not slug or not folder_id:
            return []

        resp = self._api(slug, "files", {"folderId": folder_id})
        # Token can expire across a long run; re-fetch the page once and retry.
        if resp is None:
            self._handshake.pop(slug, None)
            resp = self._api(slug, "files", {"folderId": folder_id})

        files = (resp or {}).get("files") or []
        documents = []
        for f in files:
            file_url = f.get("fileUrl")
            if not file_url:
                continue
            download_url = self._clean_url(file_url)
            name = (f.get("name") or "").strip()
            filename = download_url.rsplit("/", 1)[-1]
            documents.append({
                "download_url": download_url,
                "doc_type": self._classify_doc(name or filename),
                "title": name or filename,
                "filename": filename,
                "posted_date": self._norm_date(f.get("date")),
            })
        return documents

    # ── Helpers ──────────────────────────────────────────────────

    def _get_handshake(self, slug):
        """Fetch a committee page and extract auth token, plid, portletId.

        Cached per slug; the GET also seeds the session with the cookies the
        committeefile API needs.
        """
        if slug in self._handshake:
            return self._handshake[slug]

        url = f"{self.BASE_URL}/{slug}"
        try:
            self._polite_delay()
            html = self.session.get(url, timeout=30).text
        except Exception as e:
            print(f"    [{slug}] page fetch failed: {e}")
            return None

        tok = self._AUTH_TOKEN_RE.search(html)
        plid = self._PLID_RE.search(html)
        portlet = self._PORTLET_RE.search(html)
        if not (tok and plid and portlet):
            print(f"    [{slug}] no file-browser handshake on page")
            return None

        hs = {
            "auth": tok.group(1),
            "plid": plid.group(1),
            "portletId": "portlet_" + portlet.group(1),
        }
        self._handshake[slug] = hs
        return hs

    def _api(self, slug, endpoint, body):
        """POST to a committeefile endpoint. Returns parsed JSON or None."""
        hs = self._get_handshake(slug)
        if not hs:
            return None
        payload = {"plid": hs["plid"], "portletId": hs["portletId"], **body}
        url = f"{self.API_BASE}/{endpoint}?p_auth={hs['auth']}"
        try:
            self._polite_delay()
            resp = self.session.post(
                url, json=payload, timeout=30,
                headers={"x-csrf-token": hs["auth"],
                         "Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception as e:
            print(f"    [{slug}] {endpoint} failed: {e}")
            return None

    @staticmethod
    def _clean_url(file_url):
        """Drop the trailing Liferay /{uuid} so the URL ends in the filename,
        and percent-encode spaces. Both forms serve the file; the no-uuid
        form gives the base scraper a properly-extensioned local filename."""
        last = file_url.rsplit("/", 1)[-1]
        if _UUID_RE.match(last):
            file_url = file_url.rsplit("/", 1)[0]
        return file_url.replace(" ", "%20")

    @staticmethod
    def _norm_date(date_str):
        """'2026/06/03' or '2026-06-03' → 'YYYY-MM-DD'."""
        if not date_str:
            return None
        for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(date_str.strip(), fmt).strftime(
                    "%Y-%m-%d")
            except ValueError:
                continue
        return None

    # ── Maintenance ──────────────────────────────────────────────

    @classmethod
    def discover_committee_pages(cls):
        """Re-discover every /committees page with a working file browser.

        Prints a COMMITTEE_PAGES-ready list. Run ad hoc when NYISO adds or
        retires a committee; not used by the scrape pipeline.
        """
        import requests
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin

        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(s.get(cls.CALENDAR_URL, timeout=30).text, "lxml")

        cands = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/") and not href.startswith("//"):
                href = urljoin(cls.BASE_URL, href)
            if "nyiso.com" not in href:
                continue
            path = href.split("nyiso.com", 1)[-1].split("?")[0].rstrip("/")
            if path.count("/") != 1 or not path:
                continue
            cands.setdefault(href.split("?")[0], a.get_text(strip=True))

        by_instance = {}
        for url, text in sorted(cands.items()):
            try:
                html = s.get(url, timeout=20).text
            except Exception:
                continue
            if "<nyiso-committee-file-browser" not in html:
                continue
            m = cls._PORTLET_RE.search(html)
            inst = m.group(1).rsplit("_", 1)[-1] if m else url
            slug = url.split("nyiso.com/", 1)[-1]
            if inst not in by_instance or len(slug) < len(by_instance[inst][1]):
                by_instance[inst] = (text.strip(), slug)

        for name, slug in sorted(by_instance.values()):
            print(f'        ("{name}", "{slug}"),')


def main():
    """Run the NYISO scraper standalone."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from db.database import init_db

    init_db()
    NYISOScraper().run(lookback_days=14, lookahead_days=30, download=True)


if __name__ == "__main__":
    main()
