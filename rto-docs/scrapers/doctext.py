#!/usr/bin/env python3
"""
Structure-preserving text extraction for AI screening.

Why this module exists: the original extractors flattened documents into
word soup before Haiku ever saw them, which is where most summary
mis-attribution came from. Specific failure modes this fixes:

  * PDF tables  — page.extract_text() linearizes a table in x/y order, so
    a value can land next to the wrong row label. Here, tables found by
    pdfplumber are ALSO rendered as pipe-delimited rows (| a | b |) after
    the page prose, so row/column attribution survives. Table content can
    thus appear twice; the screening prompt marks the pipe rows as the
    authoritative reading. (Prose is deliberately NOT filtered by table
    bbox — pdfplumber's cell grid sometimes misses text that visually
    sits in the table, and filtering would silently drop it.)
  * Slide decks — text runs were joined with spaces (splitting tokens
    like "NPRR" + "1214") and, in SPP's path, every newline collapsed so
    a whole deck became one line. Here, runs within a paragraph
    concatenate exactly as PowerPoint stores them, paragraphs break on
    </a:p>, slides carry [Slide N] markers, and in-slide tables (a:tbl)
    render as pipe rows.
  * Charts      — chart values live in separate chart XML parts that were
    never read; at best stray data labels floated free of any series
    name. Here, each slide's relationship file is followed to its chart
    parts and series are rendered as "Chart 'name': cat=val; ...".
  * Word tables — every cell ended up on its own line (column soup).
    Here w:tbl/w:tr/w:tc render as pipe rows, innermost tables first so
    nested layout tables don't scramble the output.

The pipe-row + [Slide N] / [Page N] conventions are documented in the
screening prompt (screen_documents.py) so the model knows how to read
them — and knows NOT to attribute a number whose row/column is unclear.

Everything degrades gracefully: any per-page/per-slide/per-table failure
falls back to plain text for that unit, never raises.
"""

import re
import zipfile
from html import unescape

# ── Shared helpers ───────────────────────────────────────────────────

def _pipe_row(cells):
    cells = [re.sub(r"\s+", " ", (c or "")).strip() for c in cells]
    while cells and not cells[-1]:
        cells.pop()          # trailing empties carry no position info
    if not any(cells):
        return None
    return "| " + " | ".join(cells) + " |"


def _tidy(text):
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


# ── PDF ──────────────────────────────────────────────────────────────

def pdf_to_text(src, max_pages=50):
    """src is a path or file-like. Tables become pipe rows; prose is
    extracted with table regions excluded; pages carry [Page N] markers
    (presentation PDFs are one slide per page, so the marker tells the
    model what belongs together)."""
    try:
        import pdfplumber
    except ImportError:
        return None
    try:
        chunks = []
        with pdfplumber.open(src) as pdf:
            multi = len(pdf.pages) > 1
            for i, page in enumerate(pdf.pages[:max_pages], 1):
                parts = []
                # Keep only GENUINE tables: at least two rows with two or
                # more populated cells. Slide PDFs often trip find_tables
                # on decorative frames, yielding one mega-cell per line —
                # those regions must stay in the prose pass instead, or
                # the content duplicates and the layout gets worse.
                tables = []
                try:
                    for t in page.find_tables():
                        rows = t.extract() or []
                        structured = sum(
                            1 for r in rows
                            if sum(1 for c in r if (c or "").strip()) >= 2)
                        if structured >= 2:
                            rendered = [_pipe_row(r) for r in rows]
                            rendered = [r for r in rendered if r]
                            if rendered:
                                tables.append((t.bbox, "\n".join(rendered)))
                except Exception:
                    tables = []
                # Prose is the FULL page text — table regions are not
                # filtered out. Filtering loses words whenever pdfplumber's
                # cell grid misses text that visually sits in the table
                # (seen on PJM decks: an entire first column vanished). So
                # table content can appear twice — in prose flow and as
                # pipe rows. The screening prompt says the pipe rows are
                # authoritative for attribution; duplication only costs
                # excerpt budget, never content.
                try:
                    prose = page.extract_text()
                except Exception:
                    prose = None
                if prose and prose.strip():
                    parts.append(prose.strip())
                parts.extend(rendered for _, rendered in tables)
                if parts:
                    head = f"[Page {i}]\n" if multi else ""
                    chunks.append(head + "\n".join(parts))
        return _tidy("\n\n".join(chunks)) if chunks else None
    except Exception:
        return None


# ── Word (.docx) ─────────────────────────────────────────────────────

_W_TBL_INNER = re.compile(r"<w:tbl[ >](?:(?!<w:tbl[ >]).)*?</w:tbl>", re.S)
_W_TR = re.compile(r"<w:tr[ >].*?</w:tr>", re.S)
_W_TC = re.compile(r"<w:tc[ >].*?</w:tc>", re.S)


def docx_xml_to_text(xml):
    """word/document.xml → text with tables as pipe rows (innermost
    tables first, so nested layout tables stay coherent)."""
    xml = xml.replace("<w:tab/>", " ")
    xml = re.sub(r"<w:br\s*/?>", "\n", xml)

    def render_tbl(m):
        rows = []
        for tr in _W_TR.findall(m.group(0)):
            cells = []
            for tc in _W_TC.findall(tr):
                t = re.sub(r"</w:p>", " ", tc)
                t = re.sub(r"<[^>]+>", "", t)
                cells.append(unescape(t))
            row = _pipe_row(cells)
            if row:
                rows.append(row)
        # \x00 shields already-rendered rows from the outer tag-strip.
        return "\x00" + "\n".join(rows) + "\x00" if rows else ""

    while _W_TBL_INNER.search(xml):
        xml = _W_TBL_INNER.sub(render_tbl, xml, count=0)

    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return _tidy(unescape(xml).replace("\x00", "\n"))


def docx_to_text(src):
    """src is a path or file-like of a .docx."""
    try:
        with zipfile.ZipFile(src) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except Exception:
        return None
    return docx_xml_to_text(xml)


# ── PowerPoint (.pptx) ───────────────────────────────────────────────

_A_TBL = re.compile(r"<a:tbl>.*?</a:tbl>", re.S)
_A_TR = re.compile(r"<a:tr[ >].*?</a:tr>", re.S)
_A_TC = re.compile(r"<a:tc[ >]?.*?</a:tc>", re.S)
_A_T = re.compile(r"<a:t>(.*?)</a:t>", re.S)
_SLIDE_NAME = re.compile(r"ppt/slides/slide(\d+)\.xml$")


def _runs_text(fragment):
    """Concatenate a:t runs exactly as stored — PowerPoint keeps literal
    spacing inside runs, so joining with '' never splits a token."""
    return unescape("".join(_A_T.findall(fragment)))


def slide_xml_to_text(xml):
    """One slide's XML → paragraphs on their own lines, a:tbl tables as
    pipe rows, in document order."""
    out = []
    pos = 0
    for m in _A_TBL.finditer(xml):
        out.append(_paragraphs(xml[pos:m.start()]))
        rows = []
        for tr in _A_TR.findall(m.group(0)):
            row = _pipe_row([_runs_text(tc) for tc in _A_TC.findall(tr)])
            if row:
                rows.append(row)
        out.append("\n".join(rows))
        pos = m.end()
    out.append(_paragraphs(xml[pos:]))
    return _tidy("\n".join(p for p in out if p)) or ""


def _paragraphs(fragment):
    paras = []
    # <a:br/> is an in-paragraph line break (shift+enter) — split on it
    # too, or runs on either side glue into one token.
    for p in re.split(r"</a:p>|<a:br\s*/?>", fragment):
        text = re.sub(r"\s+", " ", _runs_text(p)).strip()
        if text:
            paras.append(text)
    return "\n".join(paras)


def chart_xml_to_text(xml, max_points=40):
    """Chart part XML → 'Chart "series": cat=val; ...' lines so values
    stay attributed to their series instead of floating free."""
    lines = []
    for ser in re.findall(r"<c:ser>.*?</c:ser>", xml, re.S):
        name_m = re.search(r"<c:tx>.*?<c:v>(.*?)</c:v>", ser, re.S)
        name = unescape(name_m.group(1)).strip() if name_m else "series"
        cat_m = re.search(r"<c:cat>(.*?)</c:cat>", ser, re.S)
        val_m = re.search(r"<c:val>(.*?)</c:val>", ser, re.S)
        cats = ([unescape(v) for v in re.findall(r"<c:v>(.*?)</c:v>", cat_m.group(1))]
                if cat_m else [])
        vals = ([unescape(v) for v in re.findall(r"<c:v>(.*?)</c:v>", val_m.group(1))]
                if val_m else [])
        if not vals:
            continue
        pairs = []
        for i, v in enumerate(vals[:max_points]):
            label = cats[i] if i < len(cats) else f"#{i + 1}"
            pairs.append(f"{label}={v}")
        lines.append(f'Chart "{name}": ' + "; ".join(pairs))
    return "\n".join(lines)


def pptx_to_text(src, max_slides=80):
    """src is a path or file-like of a .pptx/.potx. Slides carry
    [Slide N] markers; each slide's charts (via its rels part) are
    appended under that slide."""
    try:
        with zipfile.ZipFile(src) as z:
            names = set(z.namelist())
            slides = sorted(
                (n for n in names if _SLIDE_NAME.search(n)),
                key=lambda n: int(_SLIDE_NAME.search(n).group(1)))
            chunks = []
            for name in slides[:max_slides]:
                num = _SLIDE_NAME.search(name).group(1)
                try:
                    body = slide_xml_to_text(
                        z.read(name).decode("utf-8", "replace"))
                except Exception:
                    body = ""
                for chart in _slide_charts(z, names, num):
                    body = (body + "\n" if body else "") + chart
                if body:
                    chunks.append(f"[Slide {num}]\n{body}")
        return _tidy("\n\n".join(chunks)) if chunks else None
    except Exception:
        return None


def _slide_charts(z, names, slide_num):
    """Follow slideN.xml.rels to chart parts; yield rendered chart text."""
    rels_name = f"ppt/slides/_rels/slide{slide_num}.xml.rels"
    if rels_name not in names:
        return
    try:
        rels = z.read(rels_name).decode("utf-8", "replace")
    except Exception:
        return
    for target in re.findall(
            r'Target="([^"]*charts/chart\d+\.xml)"', rels):
        part = "ppt/" + target.replace("../", "")
        if part not in names:
            continue
        try:
            text = chart_xml_to_text(z.read(part).decode("utf-8", "replace"))
            if text:
                yield text
        except Exception:
            continue


# ── Word 97-2003 (.doc) ──────────────────────────────────────────────

def doc_to_text(src):
    """Best-effort text from legacy OLE2 .doc (no zip-of-XML, no stdlib
    parser). Pulls printable UTF-16LE and cp1252 runs from the
    WordDocument stream in offset order rather than implementing the
    FIB/piece-table spec. Noisier than real parsing (deleted-text
    scratch areas can leak in) but plenty for relevance screening."""
    try:
        import olefile
    except ImportError:
        return None
    try:
        with olefile.OleFileIO(src) as ole:
            if not ole.exists("WordDocument"):
                return None
            data = ole.openstream("WordDocument").read()
    except Exception:
        return None

    runs = []
    for m in re.finditer(rb"(?:[\x20-\x7e\xa0-\xff]\x00){12,}", data):
        runs.append((m.start(), m.group().decode("utf-16-le", "ignore")))
    covered = [(s, s + len(t) * 2) for s, t in runs]
    for m in re.finditer(rb"[\x20-\x7e\xa0-\xff\r\t]{24,}", data):
        if any(a <= m.start() < b for a, b in covered):
            continue
        runs.append((m.start(), m.group().decode("cp1252", "ignore")))

    runs.sort()
    text = "\n".join(t.replace("\r", "\n") for _, t in runs)
    text = _tidy(text)
    # A real document yields paragraphs; a few stray runs means the file
    # is mostly non-text structures — treat as no extraction.
    return text if text and len(text) >= 200 else None
