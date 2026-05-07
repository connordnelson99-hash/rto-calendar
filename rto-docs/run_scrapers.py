#!/usr/bin/env python3
"""
RTO Document Scraper Runner

Orchestrates scraping across multiple RTOs and exports results
to a JSON file compatible with the web calendar.
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from db.database import (
    init_db, get_connection, get_stats,
    export_calendar_json, export_issues_json,
    resolve_issue_references,
)

# Import scrapers
from scrapers.pjm_scraper import PJMScraper
from scrapers.caiso_scraper import CAISOScraper
from scrapers.ferc_scraper import FERCScraper
from scrapers.isone_scraper import ISONEScraper
from scrapers.pjm_issues_scraper import PJMIssuesScraper
from scrapers.caiso_issues_scraper import CAISOIssuesScraper

SCRAPER_REGISTRY = {
    "PJM": PJMScraper,
    "CAISO": CAISOScraper,
    "FERC": FERCScraper,
    "ISO-NE": ISONEScraper,
}

ISSUES_SCRAPER_REGISTRY = {
    "PJM": PJMIssuesScraper,
    "CAISO": CAISOIssuesScraper,
}

OUTPUT_JSON = Path(__file__).parent / "rto_events_with_docs.json"
OUTPUT_ISSUES_JSON = Path(__file__).parent / "rto_issues.json"


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
        conn.close()
        return

    # Run scrapers
    rtos_to_scrape = (
        list(SCRAPER_REGISTRY.keys()) if args.rto == "ALL"
        else [args.rto]
    )

    if not args.issues_only:
        for rto_name in rtos_to_scrape:
            scraper_class = SCRAPER_REGISTRY[rto_name]
            scraper = scraper_class()
            scraper.run(
                lookback_days=args.lookback,
                lookahead_days=args.lookahead,
                download=not args.no_download,
            )

    if not args.no_issues:
        for rto_name in rtos_to_scrape:
            issues_class = ISSUES_SCRAPER_REGISTRY.get(rto_name)
            if issues_class is None:
                continue
            issues_class().run(refresh_closed=args.refresh_closed_issues)

    # Export JSON
    conn = get_connection()
    resolve_issue_references(conn)
    export_calendar_json(conn, args.output)
    export_issues_json(conn, str(OUTPUT_ISSUES_JSON))
    conn.close()

    print(f"\nCalendar JSON exported to: {args.output}")
    print(f"Issues JSON exported to: {OUTPUT_ISSUES_JSON}")


if __name__ == "__main__":
    main()
