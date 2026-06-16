#!/usr/bin/env python3
"""
RTO Document Scraper Runner

Orchestrates scraping across multiple RTOs and exports results
to a JSON file compatible with the web calendar.
"""

import argparse
import sys
import traceback
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from db.database import (
    init_db, get_connection, get_stats,
    export_calendar_json, export_issues_json,
    export_hydro_corpus, resolve_issue_references,
)

# Import scrapers
from scrapers.pjm_scraper import PJMScraper
from scrapers.caiso_scraper import CAISOScraper
from scrapers.ferc_scraper import FERCScraper
from scrapers.isone_scraper import ISONEScraper
from scrapers.nyiso_scraper import NYISOScraper
from scrapers.spp_scraper import SPPScraper
from scrapers.miso_scraper import MISOScraper
from scrapers.ercot_scraper import ERCOTScraper
from scrapers.pjm_issues_scraper import PJMIssuesScraper
from scrapers.caiso_issues_scraper import CAISOIssuesScraper
from scrapers.isone_issues_scraper import ISONEIssuesScraper
from scrapers.ercot_issues_scraper import ERCOTIssuesScraper

SCRAPER_REGISTRY = {
    "PJM": PJMScraper,
    "CAISO": CAISOScraper,
    "FERC": FERCScraper,
    "ISO-NE": ISONEScraper,
    "NYISO": NYISOScraper,
    "SPP": SPPScraper,
    "MISO": MISOScraper,
    "ERCOT": ERCOTScraper,
}

ISSUES_SCRAPER_REGISTRY = {
    "PJM": PJMIssuesScraper,
    "CAISO": CAISOIssuesScraper,
    "ISO-NE": ISONEIssuesScraper,
    "ERCOT": ERCOTIssuesScraper,
}

OUTPUT_JSON = Path(__file__).parent / "rto_events_with_docs.json"
OUTPUT_ISSUES_JSON = Path(__file__).parent / "rto_issues.json"
OUTPUT_CORPUS_JSON = Path(__file__).parent / "rto_hydro_corpus.json"
OUTPUT_CORPUS_CSV = Path(__file__).parent / "rto_hydro_corpus.csv"

# Breadcrumb the CI job gates on. A scraper that raises (e.g. CAISO/ISO-NE
# returning 403 when a site's bot filter blocks the runner IP) is caught so
# the healthy RTOs still publish; its name is written here so the workflow's
# final step can exit non-zero and still send the usual GitHub failure email.
LOGS_DIR = Path(__file__).parent / "logs"
FAILURE_MARKER = LOGS_DIR / "scraper_failures.txt"


def _record_scraper_failures(failed):
    """Write (or clear) the failure marker the CI job gates on."""
    # Always start clean so a stale marker from a prior local run can't
    # trigger a false alert on an otherwise-healthy run.
    FAILURE_MARKER.unlink(missing_ok=True)
    if not failed:
        print("\nAll scrapers completed without errors.")
        return
    LOGS_DIR.mkdir(exist_ok=True)
    FAILURE_MARKER.write_text("\n".join(failed) + "\n", encoding="utf-8")
    print(f"\n!! {len(failed)} scraper(s) failed: {', '.join(failed)}",
          file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Scrape RTO/ISO meeting documents"
    )
    parser.add_argument(
        "--rto",
        choices=list(SCRAPER_REGISTRY.keys()) + ["ALL"],
        default="ALL",
        help="Which RTO to scrape (default: ALL)",
    )
    parser.add_argument(
        "--lookback", type=int, default=14,
        help="Days to look back (default: 14)",
    )
    parser.add_argument(
        "--lookahead", type=int, default=30,
        help="Days to look ahead (default: 30)",
    )
    parser.add_argument(
        "--no-download", action="store_true",
        help="Scrape metadata only, don't download files",
    )
    parser.add_argument(
        "--export-only", action="store_true",
        help="Just re-export JSON from existing database",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Show database statistics and exit",
    )
    parser.add_argument(
        "--output", type=str, default=str(OUTPUT_JSON),
        help=f"Output JSON path (default: {OUTPUT_JSON})",
    )
    parser.add_argument(
        "--no-issues", action="store_true",
        help="Skip issue-tracking scrapers (PJM Issue Tracking, etc.)",
    )
    parser.add_argument(
        "--issues-only", action="store_true",
        help="Run only issue-tracking scrapers, skip meeting/document scraping",
    )
    parser.add_argument(
        "--refresh-closed-issues", action="store_true",
        help="Re-fetch detail pages for closed issues too (default: active only)",
    )

    args = parser.parse_args()

    # Initialize database
    init_db()

    if args.stats:
        conn = get_connection()
        stats = get_stats(conn)
        conn.close()

        if not stats:
            print("No data in database yet.")
            return

        print("\nDatabase Statistics:")
        print("-" * 40)
        for rto, data in stats.items():
            print(f"  {rto}:")
            print(f"    Meetings:   {data['meetings']}")
            print(f"    Documents:  {data['documents']}")
            print(f"    Downloaded: {data['downloaded']}")
        return

    if args.export_only:
        conn = get_connection()
        # Re-resolve in case docs landed after the last issues scrape
        resolve_issue_references(conn)
        export_calendar_json(conn, args.output)
        export_issues_json(conn, str(OUTPUT_ISSUES_JSON))
        export_hydro_corpus(conn, str(OUTPUT_CORPUS_JSON), str(OUTPUT_CORPUS_CSV))
        conn.close()
        return

    # Run scrapers
    rtos_to_scrape = (
        list(SCRAPER_REGISTRY.keys()) if args.rto == "ALL"
        else [args.rto]
    )

    # Isolate each source: a single scraper raising (e.g. a 403 from a site
    # bot-blocking the runner) must not abort the others or block the publish.
    # Failures are collected and surfaced at the end via the failure marker.
    failed = []

    if not args.issues_only:
        for rto_name in rtos_to_scrape:
            scraper_class = SCRAPER_REGISTRY[rto_name]
            scraper = scraper_class()
            try:
                scraper.run(
                    lookback_days=args.lookback,
                    lookahead_days=args.lookahead,
                    download=not args.no_download,
                )
            except Exception as e:
                failed.append(rto_name)
                print(f"\n  !! {rto_name} scraper FAILED: {e}", file=sys.stderr)
                traceback.print_exc()

    if not args.no_issues:
        for rto_name in rtos_to_scrape:
            issues_class = ISSUES_SCRAPER_REGISTRY.get(rto_name)
            if issues_class is None:
                continue
            try:
                issues_class().run(refresh_closed=args.refresh_closed_issues)
            except Exception as e:
                failed.append(f"{rto_name} (issues)")
                print(f"\n  !! {rto_name} issues scraper FAILED: {e}",
                      file=sys.stderr)
                traceback.print_exc()

    # Export JSON
    conn = get_connection()
    resolve_issue_references(conn)
    export_calendar_json(conn, args.output)
    export_issues_json(conn, str(OUTPUT_ISSUES_JSON))
    export_hydro_corpus(conn, str(OUTPUT_CORPUS_JSON), str(OUTPUT_CORPUS_CSV))
    conn.close()

    print(f"\nCalendar JSON exported to: {args.output}")
    print(f"Issues JSON exported to: {OUTPUT_ISSUES_JSON}")
    print(f"Hydro corpus exported to: {OUTPUT_CORPUS_JSON} + .csv")

    _record_scraper_failures(failed)


if __name__ == "__main__":
    main()
