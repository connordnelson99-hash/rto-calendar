#!/usr/bin/env python3
"""
Pipeline health check — turns "quietly produced less" into a red run.

Every layer of this pipeline is built to survive a failure and keep
publishing: a scraper that can't reach its site returns no meetings, a
download that fails leaves a document without text, a screening call that
errors is skipped. That is the right call for the publish, but it means most
failure modes look exactly like a quiet week. In Sep 2026 the Anthropic
account's spend limit was hit and nothing was screened for three weeks while
every run stayed green.

This runs after the export, compares today's numbers against the recent
baseline already recorded in scrape_log and the DB, and appends a line to the
failure marker for anything out of range. The workflow's final step fails the
job when the marker exists, so the usual GitHub failure email goes out — after
today's data has been committed, never instead of it.

Always exits 0: the point is to flag, not to block.

    python check_health.py            # check and write the marker
    python check_health.py --report   # print the numbers, write nothing
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db.database import get_connection

HERE = Path(__file__).parent
FAILURE_MARKER = HERE / "logs" / "scraper_failures.txt"
CALENDAR_JSON = HERE / "rto_events_with_docs.json"

# Baseline window for per-RTO meeting counts. Two weeks of daily runs is
# enough to smooth a slow stretch without hiding a multi-day outage.
BASELINE_DAYS = 14
# An RTO that normally finds at least this many meetings and today found none
# has almost certainly been blocked or had its page change under us. Below
# this the source is too sparse to judge (FERC often legitimately finds 0).
MIN_MEDIAN_FOR_ZERO_CHECK = 3
# A drop to under this fraction of the median, on a source busy enough for
# the ratio to mean something, is flagged as a partial outage.
DROP_FRACTION = 0.4
MIN_MEDIAN_FOR_DROP_CHECK = 10
# File types the extractor handles. Anything else (xlsx, zip, ...) is skipped
# by design and must not count against the text ratio.
TEXT_SUFFIXES = (".pdf", ".docx", ".pptx", ".potx", ".doc", ".txt", ".csv")
MIN_TEXT_RATIO = 0.7
MIN_DOCS_FOR_TEXT_CHECK = 10
# Screening is expected daily. Two missed days with work waiting means the
# step is failing, timing out, or not running.
MAX_SCREENING_AGE_HOURS = 36
# Stage 2 screens at most 200 docs a run. A backlog bigger than two runs'
# worth means we are falling further behind every day.
MAX_DOC_BACKLOG = 400
# The export should always hold months of history plus the current window.
MIN_EXPORTED_EVENTS = 100
# Mirrors the Stage-2 gate in screen_documents.run_stage2.
DOCS_SCREENED_REGARDLESS = ("NYISO", "SPP", "SPP Markets +", "MISO", "ERCOT")


def _fmt_age(hours):
    if hours is None:
        return "never"
    if hours < 48:
        return f"{hours:.0f}h ago"
    return f"{hours / 24:.1f} days ago"


def check_scraper_counts(conn):
    """Each RTO's latest run against the median of its previous two weeks."""
    problems = []
    lines = []
    latest = conn.execute("""
        SELECT rto, MAX(id) AS id FROM scrape_log
        WHERE scrape_type = 'full_run' GROUP BY rto
    """).fetchall()
    for r in latest:
        row = conn.execute(
            "SELECT * FROM scrape_log WHERE id = ?", (r["id"],)).fetchone()
        base = [x[0] for x in conn.execute(f"""
            SELECT events_found FROM scrape_log
            WHERE rto = ? AND scrape_type = 'full_run' AND id < ?
              AND scraped_at >= datetime(?, '-{BASELINE_DAYS} days')
        """, (row["rto"], row["id"], row["scraped_at"])).fetchall()]
        median = statistics.median(base) if base else None
        age_h = conn.execute(
            "SELECT (julianday('now') - julianday(?)) * 24", (row["scraped_at"],)
        ).fetchone()[0]
        today = row["events_found"] or 0
        lines.append(f"  {row['rto']:<8} {today:>4} meetings today"
                     f"  (median {median if median is not None else '—'},"
                     f" n={len(base)}, {row['status']}, {_fmt_age(age_h)})")

        if age_h > 30:
            problems.append(
                f"{row['rto']} scraper last logged a run {_fmt_age(age_h)} — "
                f"it did not run today")
            continue
        if row["status"] != "success":
            # Already red via the orchestrator's marker; nothing to add.
            continue
        if median is None:
            continue
        if today == 0 and median >= MIN_MEDIAN_FOR_ZERO_CHECK:
            problems.append(
                f"{row['rto']} found 0 meetings (median {median:g} over "
                f"{BASELINE_DAYS}d) — likely blocked or page layout changed")
        elif median >= MIN_MEDIAN_FOR_DROP_CHECK and today < DROP_FRACTION * median:
            problems.append(
                f"{row['rto']} found {today} meetings vs median {median:g} — "
                f"partial outage?")
    return problems, lines


def check_text_extraction(conn):
    """Share of recently-seen text-bearing documents that actually got text."""
    rows = conn.execute("""
        SELECT lower(download_url) AS url,
               (extracted_text IS NOT NULL AND extracted_text <> '') AS has_text
        FROM documents
        WHERE first_seen_at >= datetime('now', '-7 days')
    """).fetchall()
    eligible = [r for r in rows
                if r["url"] and r["url"].split("?")[0].endswith(TEXT_SUFFIXES)]
    with_text = sum(1 for r in eligible if r["has_text"])
    n = len(eligible)
    ratio = with_text / n if n else None
    line = (f"  {with_text}/{n} text-bearing docs seen in the last 7 days "
            f"have extracted text"
            + (f" ({ratio:.0%})" if ratio is not None else ""))
    problems = []
    if n >= MIN_DOCS_FOR_TEXT_CHECK and ratio < MIN_TEXT_RATIO:
        problems.append(
            f"only {ratio:.0%} of {n} recent PDF/Office documents have text "
            f"(threshold {MIN_TEXT_RATIO:.0%}) — download or extraction is failing")
    return problems, [line]


def check_screening(conn):
    """Screening ran recently if there was anything to screen, and the
    backlog is small enough to clear."""
    problems = []
    lines = []
    gated = ", ".join(f"'{r}'" for r in DOCS_SCREENED_REGARDLESS)

    newest_doc_h = conn.execute("""
        SELECT (julianday('now') - julianday(MAX(ai_processed_at))) * 24
        FROM documents""").fetchone()[0]
    newest_mtg_h = conn.execute("""
        SELECT (julianday('now') - julianday(MAX(meeting_screened_at))) * 24
        FROM meetings""").fetchone()[0]

    # Work that is waiting *and* inside the scraper's window, i.e. what the
    # calendar's readers would be looking at right now.
    pending_docs = conn.execute(f"""
        SELECT COUNT(*) FROM documents d JOIN meetings m ON m.id = d.meeting_id
        WHERE (m.hydro_relevant = 1 OR d.rto IN ({gated}))
          AND d.ai_processed_at IS NULL
          AND m.meeting_date BETWEEN date('now', '-16 days') AND date('now', '+16 days')
    """).fetchone()[0]
    pending_mtgs = conn.execute("""
        SELECT COUNT(*) FROM meetings
        WHERE meeting_screened_at IS NULL
          AND meeting_date BETWEEN date('now', '-16 days') AND date('now', '+16 days')
    """).fetchone()[0]
    backlog = conn.execute(f"""
        SELECT COUNT(*) FROM documents d JOIN meetings m ON m.id = d.meeting_id
        WHERE (m.hydro_relevant = 1 OR d.rto IN ({gated}))
          AND (d.ai_processed_at IS NULL OR d.stakeholders_extracted_at IS NULL)
    """).fetchone()[0]

    lines.append(f"  newest document verdict {_fmt_age(newest_doc_h)}; "
                 f"newest meeting verdict {_fmt_age(newest_mtg_h)}")
    lines.append(f"  in-window pending: {pending_mtgs} meetings, {pending_docs} docs;"
                 f" total doc backlog {backlog}")

    if pending_mtgs and (newest_mtg_h is None or newest_mtg_h > MAX_SCREENING_AGE_HOURS):
        problems.append(
            f"{pending_mtgs} in-window meetings unscreened and the last meeting "
            f"verdict was {_fmt_age(newest_mtg_h)} — Stage 1 is not running")
    if pending_docs and (newest_doc_h is None or newest_doc_h > MAX_SCREENING_AGE_HOURS):
        problems.append(
            f"{pending_docs} in-window documents unscreened and the last document "
            f"verdict was {_fmt_age(newest_doc_h)} — Stage 2 is not running")
    if backlog > MAX_DOC_BACKLOG:
        problems.append(
            f"document screening backlog is {backlog} (> {MAX_DOC_BACKLOG}) — "
            f"falling behind; raise --limit or run screening manually")
    return problems, lines


def check_export():
    """The published JSON parses, is not stub-sized, and covers this week."""
    problems = []
    if not CALENDAR_JSON.exists():
        return [f"{CALENDAR_JSON.name} missing"], [f"  {CALENDAR_JSON.name}: missing"]
    try:
        events = json.loads(CALENDAR_JSON.read_text(encoding="utf-8"))
    except Exception as e:  # truncated write, bad encoding
        return [f"{CALENDAR_JSON.name} is not valid JSON: {e}"], \
               [f"  {CALENDAR_JSON.name}: unreadable"]
    if not isinstance(events, list) or len(events) < MIN_EXPORTED_EVENTS:
        problems.append(
            f"{CALENDAR_JSON.name} holds {len(events) if isinstance(events, list) else '?'}"
            f" events (< {MIN_EXPORTED_EVENTS})")
    from datetime import date, timedelta
    today = date.today()
    lo, hi = (today - timedelta(days=7)).isoformat(), (today + timedelta(days=7)).isoformat()
    near = sum(1 for e in events if isinstance(e, dict) and lo <= (e.get("date") or "") <= hi)
    if near == 0:
        problems.append(f"{CALENDAR_JSON.name} has no events within ±7 days of today")
    size_mb = CALENDAR_JSON.stat().st_size / 1048576
    return problems, [f"  {CALENDAR_JSON.name}: {len(events)} events, {near} within ±7d, {size_mb:.1f} MB"]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", action="store_true",
                        help="print the numbers only; don't write the failure marker")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    conn = get_connection()
    sections = [
        ("Scrapers (meetings found vs. 14-day median)", check_scraper_counts(conn)),
        ("Text extraction", check_text_extraction(conn)),
        ("Screening", check_screening(conn)),
        ("Export", check_export()),
    ]
    conn.close()

    print(f"\n{'=' * 60}\n  Pipeline health\n{'=' * 60}")
    problems = []
    for title, (probs, lines) in sections:
        print(f"\n  {title}")
        for line in lines:
            print(line)
        problems.extend(probs)

    if not problems:
        print("\n  OK — all checks within range.")
        return

    print(f"\n  !! {len(problems)} problem(s):", file=sys.stderr)
    for p in problems:
        print(f"     - {p}", file=sys.stderr)
        # GitHub Actions annotation: shows on the run summary page.
        print(f"::error title=Pipeline health::{p}")

    if args.report:
        return
    FAILURE_MARKER.parent.mkdir(exist_ok=True)
    with FAILURE_MARKER.open("a", encoding="utf-8") as f:
        for p in problems:
            f.write(f"health: {p}\n")


if __name__ == "__main__":
    main()
