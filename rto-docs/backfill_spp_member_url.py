#!/usr/bin/env python3
"""
Backfill documents.member_url for SPP rows scraped before the column existed.

SPP document rows address a file *inside* a zip: the download_url is the
bundle plus a '#z=<entry>' fragment. That is a pipeline address, not a link —
browsers drop the fragment, and SPP re-posts bundles under new filenames
("... (4).zip", "... Updated Meeting Materials.zip", typo fixes), which 404s
the old URL. member_url carries the folder page instead, keyed by folder id,
which survives the re-upload.

Going forward the scraper sets member_url directly from the folder it read the
file out of. This fills in rows that predate that. The meeting's materials_url
is the same folder page (spp_scraper sets it to folders[0]), so it is the right
source here; where a meeting had several folders it may name a sibling folder
of the same committee, and the next scrape overwrites it with the exact one.

Idempotent: only touches rows where member_url IS NULL.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db.database import get_connection, migrate_db  # noqa: E402


def backfill(conn, dry_run=False):
    # Only rows whose download_url isn't usable as a link: a zip member
    # ('#z=' fragment) or a bare bundle. Loose files (minutes, standalone
    # agenda PDFs) already point at the exact document and are left alone —
    # a folder page would be a downgrade for them.
    rows = conn.execute("""
        SELECT d.id, d.download_url, m.materials_url
        FROM documents d
        JOIN meetings m ON m.id = d.meeting_id
        WHERE d.rto LIKE 'SPP%'
          AND d.member_url IS NULL
          AND (d.download_url LIKE '%#z=%'
               OR LOWER(d.download_url) LIKE '%.zip')
          AND m.materials_url LIKE '%/spp-documents-filings/?id=%'
    """).fetchall()

    if not dry_run:
        conn.executemany(
            "UPDATE documents SET member_url = ? WHERE id = ?",
            [(r["materials_url"], r["id"]) for r in rows])
        conn.commit()

    # Same predicate as above: rows that still NEED a member_url and lack one.
    # Loose-file rows are excluded, so a clean run reports 0 rather than
    # flagging documents that are correctly linked already.
    remaining = conn.execute("""
        SELECT COUNT(*) AS n FROM documents
        WHERE rto LIKE 'SPP%'
          AND member_url IS NULL
          AND (download_url LIKE '%#z=%' OR LOWER(download_url) LIKE '%.zip')
    """).fetchone()["n"]
    return len(rows), remaining


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    conn = get_connection()
    migrate_db(conn)
    filled, remaining = backfill(conn, dry_run=dry)
    conn.close()
    verb = "would fill" if dry else "filled"
    print(f"{verb} {filled} SPP document rows")
    if remaining:
        print(f"  {remaining} SPP rows still without member_url "
              f"(meeting has no folder-page materials_url)")
