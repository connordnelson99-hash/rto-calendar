#!/usr/bin/env python3
"""
Southwest Power Pool (SPP) — Western Energy Services Document Scraper

Scope: SPP's *Western* stakeholder process — Markets+, WRAP, and Western RC
Services. (WEIS was discontinued 2026-04-01; its archived materials still
surface but no new WEIS meetings post.) The SPP *Eastern* RTO process
(Integrated Marketplace: MOPC, MWG, …) is deliberately out of scope — flip
EVENT_TYPE to "RTO Stakeholder Meeting" to add it later.

One scraper, two UI tracks: each meeting is tagged rto="SPP Markets +" (the
Markets+ market stakeholder process — MPEC, MSWG, MTWG, MORWG, MGHGTF, MDWG,
MSC, MRATF, MSUF/MCUF, …) or rto="SPP" (everything else Western — WRAP, Western
RC, ECCWG, the MMU state-of-the-market call). The UI keys filters + colours off
that rto value, so the two render as separate, separately-coloured tracks (see
_track_rto + webcal-v2/data.js RTO_META). rto_name stays "SPP" for the registry
key, docs dir, and scrape log; the per-meeting rto is set in scrape_meetings.

Like ISO-NE and NYISO, SPP is scrapeable with plain `requests` — no Playwright.
Everything is server-rendered HTML.

── Architecture (confirmed via live probe 2026-06-10) ──────────────────────

Three page types, no JSON API:

1. Events list (the meeting enumerator)
     GET /events/?start=YYYY-MM-DD&end=YYYY-MM-DD
   Server-rendered. Structure:
     .event-row
       .event-header        -> the calendar date ("… Wednesday June 10, 2026")
       .event (1..N)
         .w25               -> start time ("2:00 PM")
         .w75 > a[href]     -> title + link to /calendar-list/{slug}/
   The page is duplicated for desktop + mobile layouts, so the same meeting
   appears twice — we dedupe by detail href. The slug's trailing 8 digits are
   the meeting date (a range like 20260127-28 for multi-day events).

   IMPORTANT — the "Event Type" dropdown (param `et`) does NOT filter
   server-side: the server returns the whole SPP calendar regardless, and the
   page's JavaScript hides rows client-side. The only per-event category
   signal is the inline LINK COLOR, and the page legend maps each color to a
   type — e.g. #a142c0 = "Western Stakeholder Meeting", #12284b = "RTO
   Stakeholder Meeting", #1fbf92 = "Training". We parse that legend and keep
   only events whose color maps to EVENT_TYPE. (Don't trust `et=`; it's a
   no-op. Verified 2026-06-10.)

2. Meeting detail page
     GET /calendar-list/{slug}/
   .event-detail carries label/value pairs:
     TIME: "December 18, 2025 10:00AM - 12:00PM"  (Central time)
   .working-group a   -> the CANONICAL committee name (+ stable group URL)
   .contact-phone     -> format/location ("Web/Phone Conference")
   .document-list .doc-col a[href^="/spp-documents-filings/?id="]
                      -> one or more per-committee document folders

3. Document folder (per committee, ALL dates)
     GET /spp-documents-filings/?id={id}
   a[href^="/Documents/{docId}/{filename}"]  — the actual downloads.
   Filenames carry an embedded YYYYMMDD, so we bucket a folder's files to a
   meeting by matching that date (like ISO-NE's one-call-per-committee model).

── The .zip problem, and the HTTP-Range trick ──────────────────────────────

SPP bundles meeting materials into .zip archives that are often huge (a single
MPEC "Meeting Materials" zip ran ~400 MB) and contain mostly .docx / .pptx —
almost no loose PDFs. Downloading those in CI would be absurd, and the base
text extractor handles neither format.

But the site supports HTTP Range (Accept-Ranges: bytes), and a zip's central
directory — the full internal file listing — lives in the last few KB of the
file regardless of total size. AND every entry is STORED (not re-compressed)
inside the outer zip, because the .docx/.pptx files are already compressed.
That makes the bytes of each internal file individually addressable by range.

So we treat each text-bearing file INSIDE the zip as its own calendar document
(agenda, minutes, each MIR recommendation report, each presentation deck) —
matching the per-document granularity of the other RTOs, so screening reports
which proposals and which stakeholders are on the table per item:
  • Range-read the outer zip's central directory (~few KB) to enumerate entries.
  • For each .docx/.pdf entry (small): range-fetch just that entry and extract
    text in-memory.
  • For each .pptx/.potx deck (large, but STORED): NESTED range — the deck is
    itself a zip, so we range into its OWN central directory and pull only the
    ppt/slides/slideN.xml parts, never the embedded images. A 35 MB deck yields
    its full slide text in ~250 KB of transfer. (Slide TEXT only — text baked
    into images/chart graphics is not OCR'd.)
Each internal file becomes a document row whose download_url is the zip with a
'#z=<entry>' fragment — browsers drop the fragment and download the full bundle
(SPP doesn't serve internal files separately), while _download_document reads
the fragment to extract that one entry's text. Per-zip central directories are
cached so N items share one listing fetch. Range reads use a shorter delay than
page fetches (RANGE_DELAY) since they hit one static document, not the site.

Standalone (non-zip) files — e.g. working-group minutes posted as loose PDFs —
go through the base scraper's normal download+extract path.

── Initiatives ─────────────────────────────────────────────────────────────

No issues/initiatives scraper. SPP publishes no structured Markets+ revision-
request tracker with status/phase fields — the "Markets+ Initiative Requests"
folder holds only the *process* doc, and the individual MIRs exist only as
recommendation reports buried inside the materials zips. Same call as NYISO:
don't fabricate progress phases. screen_documents.py bypasses the Stage-1
meeting gate for SPP (its meeting titles are bare acronyms + committee with no
agenda topic), so docs are screened on their own extracted text.
"""

import io
import re
import struct
import time
import zlib
from datetime import datetime
from urllib.parse import quote, urlparse, unquote, parse_qs

from bs4 import BeautifulSoup

from .base_scraper import BaseRTOScraper
from . import doctext

_DATE8_RE = re.compile(r"(\d{8})(?:-(\d{2,4}))?")
_MONTH_DATE_RE = re.compile(r"([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})")
_TIME_RANGE_RE = re.compile(
    r"\d{1,2}:\d{2}\s*[AP]M(?:\s*-\s*\d{1,2}:\d{2}\s*[AP]M)?", re.IGNORECASE
)


class SPPScraper(BaseRTOScraper):

    rto_name = "SPP"

    BASE_URL = "https://www.spp.org"
    CALENDAR_URL = "https://www.spp.org/events/"
    EVENT_TYPE = "Western Stakeholder Meeting"

    # SPP's Western feed is split into two UI tracks (own filter + colour in
    # webcal-v2): the Markets+ market stakeholder process, vs. everything else
    # Western (WRAP, Western RC, ECCWG, …). These strings MUST match the
    # RTO_META / RTO_SOURCE_TZ keys in webcal-v2/data.js exactly. rto_name stays
    # "SPP" (registry key, docs dir, scrape log); per-meeting rto overrides it.
    RTO_MARKETSPLUS = "SPP Markets +"
    RTO_WEST = "SPP"

    # Internal files we surface as their own document rows + extract text from.
    SURFACE_EXTS = (".docx", ".pdf", ".pptx", ".potx")
    SMALL_TEXT_EXTS = (".docx", ".pdf")          # extract via full-entry range
    DECK_EXTS = (".pptx", ".potx")               # extract via nested slide range
    EXTRACT_CHAR_BUDGET = 16_000                 # per internal file (screening reads ~8K)
    ZIP_TAIL_BYTES = 256 * 1024                  # central dir lives in the tail
    PPTX_SPAN_CAP = 6 * 1024 * 1024              # one-shot span fetch only if slides are clustered
    MAX_SLIDES = 80                              # cap slide reads per deck
    SLIDE_RE = re.compile(r"ppt/slides/slide\d+\.xml$")

    # Range reads hit one static document (a CDN-served file), not the site, so
    # a lighter delay is courteous enough. PowerPoint scatters slide XML across
    # the whole file (each slide's media follows it), so a deck usually means
    # ~N small per-slide reads — keep the delay low so that stays quick.
    RANGE_DELAY = 0.15

    def __init__(self):
        super().__init__()
        # folder id -> [(download_url, link_text), ...]; one fetch per
        # committee folder shared across all that committee's meetings.
        self._folder_cache = {}
        # zip url -> (entries, total_size); shared across a zip's internal rows.
        self._cd_cache = {}
        self._last_range_time = 0

    # ── Phase 1: meetings ────────────────────────────────────────

    def scrape_meetings(self, lookback_days=14, lookahead_days=30):
        start, end = self._date_range(lookback_days, lookahead_days)
        print(f"  Fetching SPP Western events {start} -> {end} ...")

        self._polite_delay()
        resp = self.session.get(
            self.CALENDAR_URL,
            params={"start": start, "end": end},
            timeout=30,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Map legend swatch colors -> event-type names, then keep only events
        # whose link color is our EVENT_TYPE. (The `et` query param is a no-op;
        # categorization is client-side via color — see module docstring.)
        color_to_type = self._legend_map(soup)
        want_colors = {c for c, t in color_to_type.items() if t == self.EVENT_TYPE}

        # Collect (slug, detail_url, title, time) deduped by detail_url.
        raw = {}
        for row in soup.select(".event-row"):
            header = row.select_one(".event-header")
            header_date = self._parse_header_date(
                header.get_text(" ", strip=True) if header else ""
            )
            for ev in row.select(".event"):
                a = ev.select_one(".w75 a[href]")
                if not a:
                    continue
                href = a["href"].split("?")[0]
                if "/calendar-list/" not in href:
                    continue
                # Scope gate: skip anything not coloured as our event type.
                if want_colors and self._link_color(a) not in want_colors:
                    continue
                detail_url = self.BASE_URL + href if href.startswith("/") else href
                if detail_url in raw:
                    continue
                w25 = ev.select_one(".w25")
                raw[detail_url] = {
                    "slug": href.strip("/").rsplit("/", 1)[-1],
                    "title": a.get_text(" ", strip=True),
                    "w25_time": (w25.get_text(" ", strip=True) if w25 else ""),
                    "header_date": header_date,
                }

        print(f"  events page listed {len(raw)} meetings; fetching details ...")

        meetings = []
        for detail_url, info in raw.items():
            detail = self._fetch_detail(detail_url)

            # Date precedence: slug digits (only source that encodes multi-day
            # ranges) -> detail page's TIME field -> events-list day header.
            # The latter two cover slugs whose digits aren't a real date.
            date_set = self._dates_from(info["slug"])
            meeting_date = (
                min(date_set) if date_set
                else detail.get("date") or info["header_date"]
            )
            if not meeting_date:
                continue
            meeting_date = self._compact_to_iso(meeting_date)

            committee = detail.get("committee") or self._committee_from_slug(
                info["slug"]
            )
            meeting_time = detail.get("time") or (
                f"{info['w25_time']} CT" if info["w25_time"] else None
            )
            folders = detail.get("folders") or []
            rto = self._track_rto(committee, info["slug"], detail.get("wg_url", ""))

            meetings.append({
                "title": info["title"],
                "meeting_date": meeting_date,
                "meeting_time": meeting_time,
                "committee": committee,
                "rto": rto,
                "location": detail.get("location"),
                "source_url": self.CALENDAR_URL,
                "detail_url": detail_url,
                "materials_url": (folders[0] if folders else detail_url),
                # carried to phase 2:
                "_folders": folders,
                "_date_set": sorted(self._compact_to_iso(d) for d in date_set)
                             or [meeting_date],
            })

        print(f"  {len(meetings)} SPP meetings in window")
        return meetings

    @staticmethod
    def _legend_map(soup):
        """Parse the events-page legend into {#hexcolor: 'Event Type Name'}.

        Legend markup: <span class="legend-color-box" style="background-color:
        #a142c0;"></span><span> Western Stakeholder Meeting </span>. Colors are
        lower-cased and 6-digit-normalised."""
        out = {}
        for box in soup.select(".legend-color-box"):
            m = re.search(r"#([0-9a-fA-F]{3,6})", box.get("style", ""))
            if not m:
                continue
            label = box.find_next("span")
            if not label:
                continue
            out[("#" + m.group(1)).lower()] = label.get_text(" ", strip=True)
        return out

    @staticmethod
    def _link_color(a):
        """Inline 'color: #rrggbb' from an event link, lower-cased, or None."""
        m = re.search(r"color:\s*(#[0-9a-fA-F]{3,6})", a.get("style", ""))
        return m.group(1).lower() if m else None

    def _fetch_detail(self, detail_url):
        """Parse committee, time range, location, and doc-folder URLs from a
        /calendar-list/{slug}/ page. Best-effort — returns {} on failure."""
        try:
            self._polite_delay()
            html = self.session.get(detail_url, timeout=30).text
        except Exception as e:
            print(f"    detail fetch failed ({detail_url}): {e}")
            return {}

        soup = BeautifulSoup(html, "lxml")
        out = {}

        wg = soup.select_one(".working-group a")
        if wg:
            out["committee"] = wg.get_text(" ", strip=True)
            out["wg_url"] = wg.get("href", "")

        phone = soup.select_one(".contact-phone")
        if phone:
            out["location"] = phone.get_text(" ", strip=True) or None

        # TIME: value -> "December 18, 2025 10:00AM - 12:00PM"
        # The leading "Month D, YYYY" is the meeting date straight from SPP's
        # CMS — the fallback source when the slug's digits aren't a real date
        # (see _dates_from). Kept compact (YYYYMMDD) like header_date.
        for desc in soup.select(".event-desc"):
            label = desc.select_one(".label")
            if label and label.get_text(strip=True).upper().startswith("TIME"):
                if "ZONE" in label.get_text(strip=True).upper():
                    continue
                value = desc.select_one(".value")
                vtext = value.get_text(" ", strip=True) if value else ""
                m = _TIME_RANGE_RE.search(vtext)
                if m:
                    out["time"] = self._tidy_time(m.group(0)) + " CT"
                d = self._parse_header_date(vtext)
                if d:
                    out["date"] = d
                break

        folders = []
        for a in soup.select(".document-list a[href]"):
            href = a["href"]
            if "/spp-documents-filings/" in href and "id=" in href:
                folders.append(self.BASE_URL + href if href.startswith("/") else href)
        out["folders"] = list(dict.fromkeys(folders))
        return out

    # ── Phase 2: documents ───────────────────────────────────────

    def scrape_meeting_documents(self, meeting_info):
        date_set = set(meeting_info.get("_date_set") or [meeting_info["meeting_date"]])
        documents = []
        seen = set()
        for folder_url in meeting_info.get("_folders", []):
            for download_url, link_text in self._folder_files(folder_url):
                filename = unquote(urlparse(download_url).path.rsplit("/", 1)[-1])
                if not self._file_matches(filename, date_set):
                    continue
                if download_url in seen:
                    continue
                seen.add(download_url)

                if filename.lower().endswith(".zip"):
                    documents.extend(self._zip_member_docs(download_url, filename))
                else:
                    title = link_text or filename
                    documents.append({
                        "download_url": download_url,
                        "doc_type": self._spp_doc_type(filename, title),
                        "title": title,
                        "filename": filename,
                        "posted_date": self._first_iso_date(filename),
                    })
        return documents

    def _zip_member_docs(self, zip_url, zip_name):
        """One document row per surfaced file inside the zip. Each row's
        download_url is the zip + a '#z=<entry>' fragment (browser downloads the
        whole bundle; _download_document reads the fragment to extract that one
        file's text). Falls back to a single bundle row if the listing fails."""
        try:
            entries, _ = self._outer_central_dir(zip_url)
        except Exception as e:
            print(f"      zip listing failed ({zip_name}): {e}")
            entries = None
        if not entries:
            return [{
                "download_url": zip_url,
                "doc_type": self._spp_doc_type(zip_name, zip_name),
                "title": zip_name,
                "filename": zip_name,
                "posted_date": self._first_iso_date(zip_name),
            }]

        posted = self._first_iso_date(zip_name)
        rows = []
        for e in entries:
            name = e["name"]
            ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
            if ext not in self.SURFACE_EXTS:
                continue
            inner = name.rsplit("/", 1)[-1]                  # drop folder prefix
            title = f"{inner}  [in {zip_name}]"
            rows.append({
                "download_url": zip_url + "#z=" + quote(name, safe=""),
                "doc_type": self._spp_doc_type(inner, inner),
                "title": title,
                "filename": inner,
                "posted_date": self._first_iso_date(inner) or posted,
            })
        return rows

    def _folder_files(self, folder_url):
        """List a document folder's /Documents/{id}/{name} links, cached by
        folder id so a committee's meetings share one fetch."""
        fid = self._folder_id(folder_url)
        if fid in self._folder_cache:
            return self._folder_cache[fid]
        files = []
        try:
            self._polite_delay()
            html = self.session.get(folder_url, timeout=30).text
            soup = BeautifulSoup(html, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/Documents/" not in href:
                    continue
                # Percent-encode the path (filenames have spaces) but keep the
                # structure; the base downloader unquotes it back for naming.
                url = self.BASE_URL + quote(href, safe="/:?=&%") if href.startswith("/") else href
                files.append((url, a.get_text(" ", strip=True)))
        except Exception as e:
            print(f"    folder fetch failed ({folder_url}): {e}")
        self._folder_cache[fid] = files
        return files

    # ── Zip-aware download override ───────────────────────────────

    def _download_document(self, url, committee, meeting_date):
        """A '#z=<entry>' fragment means "this row is one file inside the zip" —
        range-extract that entry's text without pulling the whole archive. A
        bare .zip (listing fell back to a bundle row) is title-only. Everything
        else is a standalone file via the base downloader."""
        base, _, frag = url.partition("#")
        if frag.startswith("z="):
            internal = unquote(frag[2:])
            return self._extract_member(base, internal, committee, meeting_date)
        if urlparse(base).path.lower().endswith(".zip"):
            return None, None   # bundle fallback row — screened on title only
        return super()._download_document(url, committee, meeting_date)

    def _extract_member(self, zip_url, internal, committee, meeting_date):
        """Extract one internal file's text and stash it in a sidecar so the
        base bookkeeping has a real path to record. Returns (path, text)."""
        try:
            entries, total = self._outer_central_dir(zip_url)
            entry = next((e for e in entries if e["name"] == internal), None)
            if not entry:
                return None, None
            ext = ("." + internal.rsplit(".", 1)[-1].lower()) if "." in internal else ""
            if ext in self.SMALL_TEXT_EXTS:
                blob = self._entry_bytes(zip_url, 0, entry)
                text = self._text_from_blob(internal, blob)
            elif ext in self.DECK_EXTS:
                text = self._deck_slide_text(zip_url, entry)
            else:
                text = None
        except Exception as e:
            print(f"      member extract failed ({internal}): {e}")
            return None, None

        if not text:
            print(f"      {internal.rsplit('/', 1)[-1]}: no text")
            return None, None
        text = text[:self.EXTRACT_CHAR_BUDGET]

        zipname = unquote(urlparse(zip_url).path.rsplit("/", 1)[-1]) or "archive.zip"
        local_dir = (self.DOCS_DIR / self.rto_name.lower()
                     / self._sanitize_committee_slug(committee) / meeting_date[:7]
                     / zipname)
        local_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^\w.-]", "_", internal.rsplit("/", 1)[-1])
        sidecar = local_dir / (slug + ".txt")
        try:
            sidecar.write_text(text, encoding="utf-8")
        except Exception as e:
            print(f"      sidecar write failed: {e}")
            return None, text
        print(f"      {internal.rsplit('/', 1)[-1]}: {len(text):,} chars")
        return str(sidecar), text

    # ── Zip via HTTP Range ────────────────────────────────────────

    def _range_get(self, url, start, end):
        """Polite (RANGE_DELAY) HTTP Range GET → bytes for [start, end]."""
        elapsed = time.time() - self._last_range_time
        if elapsed < self.RANGE_DELAY:
            time.sleep(self.RANGE_DELAY - elapsed)
        r = self.session.get(
            url, headers={"Range": f"bytes={start}-{end}"}, timeout=60
        )
        self._last_range_time = time.time()
        if r.status_code not in (200, 206):
            raise ValueError(f"range request returned {r.status_code}")
        return r.content

    def _outer_central_dir(self, zip_url):
        """(entries, total_size) for the outer zip, cached per URL. Each entry:
        name, comp_size, local_header_offset (absolute), method."""
        if zip_url in self._cd_cache:
            return self._cd_cache[zip_url]
        head = self.session.head(zip_url, timeout=30, allow_redirects=True)
        total = int(head.headers.get("Content-Length") or 0)
        if not total:
            self._cd_cache[zip_url] = ([], 0)
            return [], 0
        entries = self._central_dir(zip_url, 0, total)
        self._cd_cache[zip_url] = (entries, total)
        return entries, total

    def _central_dir(self, url, region_start, region_size):
        """Parse the central directory of a zip occupying
        [region_start, region_start+region_size) within `url`. Offsets in the
        returned entries are relative to region_start. Used for both the outer
        zip (region_start=0) and a nested deck (region_start=deck data offset)."""
        tail_n = min(self.ZIP_TAIL_BYTES, region_size)
        region_end = region_start + region_size - 1
        tail = self._range_get(url, region_end - tail_n + 1, region_end)
        idx = tail.rfind(b"\x50\x4b\x05\x06")          # End Of Central Directory
        if idx < 0:
            return []
        eocd = tail[idx:idx + 22]
        cd_size = struct.unpack("<I", eocd[12:16])[0]
        cd_rel = struct.unpack("<I", eocd[16:20])[0]   # relative to region_start
        tail_rel = region_size - tail_n                # where the tail begins
        if cd_rel >= tail_rel:
            cd = tail[cd_rel - tail_rel: cd_rel - tail_rel + cd_size]
        else:
            cd = self._range_get(url, region_start + cd_rel,
                                 region_start + cd_rel + cd_size - 1)
        entries = []
        p = 0
        while p + 46 <= len(cd) and cd[p:p + 4] == b"\x50\x4b\x01\x02":
            method = struct.unpack("<H", cd[p + 10:p + 12])[0]
            comp_size = struct.unpack("<I", cd[p + 20:p + 24])[0]
            n_len = struct.unpack("<H", cd[p + 28:p + 30])[0]
            e_len = struct.unpack("<H", cd[p + 30:p + 32])[0]
            c_len = struct.unpack("<H", cd[p + 32:p + 34])[0]
            lho = struct.unpack("<I", cd[p + 42:p + 46])[0]
            name = cd[p + 46:p + 46 + n_len].decode("utf-8", "replace")
            entries.append({"name": name, "comp_size": comp_size,
                            "local_header_offset": lho, "method": method})
            p += 46 + n_len + e_len + c_len
        return entries

    def _entry_bytes(self, url, region_start, entry):
        """Range-fetch one entry (header + data in a single request) and return
        its decompressed bytes. Offsets are relative to region_start."""
        lho = region_start + entry["local_header_offset"]
        # One read covering local header + data; +4096 slack for the extra field
        # (its length lives in the local header, which differs from central dir).
        approx = 30 + len(entry["name"]) + 4096 + entry["comp_size"]
        buf = self._range_get(url, lho, lho + approx - 1)
        if buf[:4] != b"\x50\x4b\x03\x04":
            raise ValueError("bad local file header")
        n_len = struct.unpack("<H", buf[26:28])[0]
        e_len = struct.unpack("<H", buf[28:30])[0]
        ds = 30 + n_len + e_len
        data = buf[ds: ds + entry["comp_size"]]
        if entry["method"] == 0:
            return data
        return zlib.decompress(data, -15)

    def _deck_slide_text(self, zip_url, entry):
        """Nested-range a .pptx/.potx deck for its slide TEXT only. The deck is
        STORED in the outer zip, so its bytes form a sub-zip we can range into:
        read the deck's own central directory, then fetch the span covering all
        ppt/slides/slideN.xml parts in one request and pull <a:t> runs."""
        if entry["method"] != 0:
            return None   # deflated in the outer zip — can't nested-range cheaply
        # Absolute byte offset where the deck's own data (its first local header)
        # begins inside the outer zip. Read its local header to skip name+extra.
        lho = entry["local_header_offset"]
        hdr = self._range_get(zip_url, lho, lho + 4095)
        if hdr[:4] != b"\x50\x4b\x03\x04":
            return None
        n_len = struct.unpack("<H", hdr[26:28])[0]
        e_len = struct.unpack("<H", hdr[28:30])[0]
        pstart = lho + 30 + n_len + e_len
        psize = entry["comp_size"]

        slides = [e for e in self._central_dir(zip_url, pstart, psize)
                  if self.SLIDE_RE.search(e["name"])]
        if not slides:
            return None
        slides.sort(key=lambda e: self._slide_num(e["name"]))
        slides = slides[:self.MAX_SLIDES]

        span_start = min(e["local_header_offset"] for e in slides)
        span_end = max(e["local_header_offset"] + 30 + len(e["name"]) + 4096
                       + e["comp_size"] for e in slides)
        span_end = min(span_end, psize)
        buf = None
        if span_end - span_start <= self.PPTX_SPAN_CAP:
            buf = self._range_get(zip_url, pstart + span_start,
                                  pstart + span_end - 1)

        parts = []
        for e in slides:
            try:
                if buf is not None:
                    # Slice the slide's compressed bytes out of the span buffer
                    # and inflate (slide XML is deflated inside the pptx).
                    off = e["local_header_offset"] - span_start
                    n2 = struct.unpack("<H", buf[off + 26:off + 28])[0]
                    e2 = struct.unpack("<H", buf[off + 28:off + 30])[0]
                    ds = off + 30 + n2 + e2
                    comp = buf[ds: ds + e["comp_size"]]
                    raw = zlib.decompress(comp, -15) if e["method"] == 8 else comp
                else:
                    # Per-slide fallback: _entry_bytes already inflates.
                    raw = self._entry_bytes(zip_url, pstart, e)
                xml = raw.decode("utf-8", "replace")
                body = doctext.slide_xml_to_text(xml)
                if body:
                    parts.append(f"[Slide {self._slide_num(e['name'])}]\n{body}")
            except Exception:
                continue
        # (Charts are NOT pulled here — they'd cost extra range reads per
        # deck for rels + chart parts. Locally-downloaded decks get chart
        # series via doctext.pptx_to_text; SPP's in-zip decks skip them.)
        return "\n\n".join(parts).strip() or None

    @staticmethod
    def _slide_num(name):
        m = re.search(r"slide(\d+)\.xml$", name)
        return int(m.group(1)) if m else 0

    def _text_from_blob(self, name, blob):
        """Extract plain text from an in-memory .docx or .pdf entry via
        doctext (structure-preserving — tables come out as pipe rows)."""
        ext = name.rsplit(".", 1)[-1].lower()
        if ext == "docx":
            return doctext.docx_to_text(io.BytesIO(blob))
        if ext == "pdf":
            return doctext.pdf_to_text(io.BytesIO(blob), max_pages=30)
        return None

    # ── Small parsing helpers ─────────────────────────────────────

    @staticmethod
    def _folder_id(folder_url):
        qs = parse_qs(urlparse(folder_url).query)
        return (qs.get("id") or [folder_url])[0]

    @staticmethod
    def _dates_from(text):
        """Compact YYYYMMDD dates in a slug/filename, expanding ranges like
        20260127-28 -> {20260127, 20260128}. Returns a set of 8-char strings.

        Only real calendar dates survive: SPP occasionally emits slugs whose
        digit run is NOT a date — e.g. marketsplus-change-user-forum-
        face-to-face-202601006 (a 9-digit run for an Oct 6, 2026 meeting),
        which the regex bites into as 20260100, day zero. That once shipped
        meeting_date "2026-01-00" and crashed the web calendar's date math.
        Invalid candidates are dropped so callers fall back to the detail
        page / events-list header date instead."""
        candidates = set()
        for m in _DATE8_RE.finditer(text):
            start = m.group(1)
            candidates.add(start)
            tail = m.group(2)
            if tail:
                if len(tail) == 2:      # day only, same year+month
                    candidates.add(start[:6] + tail)
                elif len(tail) == 4:    # month+day, same year
                    candidates.add(start[:4] + tail)
        out = set()
        for d in candidates:
            try:
                datetime.strptime(d, "%Y%m%d")
            except ValueError:
                continue
            out.add(d)
        return out

    def _file_matches(self, filename, meeting_iso_dates):
        """True if any date embedded in the filename matches the meeting's
        date(s). Files with NO embedded date are not tied to a meeting."""
        file_dates = {self._compact_to_iso(d) for d in self._dates_from(filename)}
        return bool(file_dates & set(meeting_iso_dates))

    def _first_iso_date(self, text):
        ds = self._dates_from(text)
        return self._compact_to_iso(min(ds)) if ds else None

    @staticmethod
    def _compact_to_iso(d):
        """20260127 -> 2026-01-27; pass through if already ISO/empty."""
        if d and len(d) == 8 and d.isdigit():
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return d

    @staticmethod
    def _parse_header_date(text):
        """'… Wednesday June 10, 2026' -> '20260610' (compact) or ''."""
        m = _MONTH_DATE_RE.search(text or "")
        if not m:
            return ""
        try:
            dt = datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y"
            )
            return dt.strftime("%Y%m%d")
        except ValueError:
            return ""

    @staticmethod
    def _tidy_time(raw):
        """'10:00AM - 12:00PM' -> '10:00 AM - 12:00 PM'."""
        return re.sub(r"(?i)(\d)\s*([AP]M)", r"\1 \2", raw).strip()

    def _track_rto(self, committee, slug, wg_url):
        """Classify a Western meeting into its UI track. Markets+ groups are
        named "Markets+ …" and sit under a marketsplus-* stakeholder-group URL
        and calendar slug; anything else Western (WRAP, Western RC, ECCWG, the
        MMU state-of-the-market call, …) falls into the general SPP-West track.
        Checks committee name, slug, and the working-group link together so a
        single missing signal doesn't misfile a meeting."""
        blob = " ".join([committee or "", slug or "", wg_url or ""]).lower()
        if "marketsplus" in blob or "markets+" in blob or "markets +" in blob:
            return self.RTO_MARKETSPLUS
        return self.RTO_WEST

    def _committee_from_slug(self, slug):
        """Fallback committee name when the detail page lacks a working-group
        link: de-kebab the slug, drop the meeting-type + date suffix."""
        s = re.sub(r"-\d{8}(?:-\d{2,4})?$", "", slug)
        for suffix in ("net-conferences", "net-conference", "face-to-face",
                       "annual-meeting", "zoom-meeting", "stakeholder-call",
                       "conference-call", "meeting", "call"):
            if s.endswith("-" + suffix):
                s = s[: -(len(suffix) + 1)]
                break
        name = s.replace("-", " ").strip().title()
        name = re.sub(r"(?i)\bmarketsplus\b", "Markets+", name)
        return name or None

    def _spp_doc_type(self, filename, title):
        text = f"{filename} {title}".lower()
        if "minutes" in text:
            return "minutes"
        if "agenda" in text:
            return "agenda"
        if "materials" in text:
            return "materials"
        return self._classify_doc(text)


def main():
    """Run the SPP scraper standalone for a quick local check."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from db.database import init_db

    init_db()
    SPPScraper().run(lookback_days=14, lookahead_days=30, download=True)


if __name__ == "__main__":
    main()
