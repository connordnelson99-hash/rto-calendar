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

from db.database import init_db, get_connection, get_stats, export_calendar_json

# Import scrapers
from scrapers.pjm_scraper import PJMScraper
from scrapers.caiso_scraper import CAISOScraper
from scrapers.ferc_scraper import FERCScraper
from scrapers.isone_scraper import ISONEScraper

SCRAPER_REGISTRY = {
    "PJM": PJMScraper,
    "CAISO": CAISOScraper,
    "FERC": FERCScraper,
    "ISO-NE": ISONEScraper,
}

OUTPUT_JSON = Path(__file__).parent / "rto_events_with_docs.json"


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
        export_calendar_json(conn, args.output)
        conn.close()
        return

    # Run scrapers
    rtos_to_scrape = (
        list(SCRAPER_REGISTRY.keys()) if args.rto == "ALL"
        else [args.rto]
    )

    for rto_name in rtos_to_scrape:
        scraper_class = SCRAPER_REGISTRY[rto_name]
        scraper = scraper_class()
        scraper.run(
            lookback_days=args.lookback,
            lookahead_days=args.lookahead,
            download=not args.no_download,
        )

    # Export JSON
    conn = get_connection()
    export_calendar_json(conn, args.output)
    conn.close()

    print(f"\nCalendar JSON exported to: {args.output}")


if __name__ == "__main__":
    main()
