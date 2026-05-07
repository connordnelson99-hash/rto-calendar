#!/usr/bin/env python3
"""
SQLite database for storing RTO meeting and document metadata.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_DIR = Path(__file__).parent
DB_PATH = DB_DIR / "rto_documents.db"


def get_connection(db_path=None):
    """Get a database connection with row factory enabled."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path=None):
    """Initialize the database schema."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rto TEXT NOT NULL,
            committee TEXT,
            title TEXT NOT NULL,
            meeting_date DATE NOT NULL,
            meeting_time TEXT,
            location TEXT,
            source_url TEXT,
            detail_url TEXT,
            materials_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(rto, title, meeting_date)
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id INTEGER REFERENCES meetings(id) ON DELETE CASCADE,
            rto TEXT NOT NULL,
            doc_type TEXT,
            title TEXT,
            filename TEXT,
            download_url TEXT NOT NULL,
            local_path TEXT,
            file_size INTEGER,
            content_type TEXT,
            posted_date TEXT,
            downloaded_at TIMESTAMP,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            extracted_text TEXT,
            extracted_at TIMESTAMP,
            hydro_relevant INTEGER,
            hydro_relevance_reason TEXT,
            ai_summary TEXT,
            ai_processed_at TIMESTAMP,
            UNIQUE(download_url)
        );

        CREATE TABLE IF NOT EXISTS scrape_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rto TEXT NOT NULL,
            scrape_type TEXT,
            target_url TEXT,
            status TEXT,
            events_found INTEGER DEFAULT 0,
            docs_found INTEGER DEFAULT 0,
            docs_downloaded INTEGER DEFAULT 0,
            error_message TEXT,
            duration_seconds REAL,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_meetings_rto_date
            ON meetings(rto, meeting_date);
        CREATE INDEX IF NOT EXISTS idx_documents_meeting
            ON documents(meeting_id);
        CREATE INDEX IF NOT EXISTS idx_documents_rto
            ON documents(rto);
        CREATE INDEX IF NOT EXISTS idx_scrape_log_rto
            ON scrape_log(rto, scraped_at);
    """)
    # The issues + issue_references + caiso_event_cache tables and their
    # indexes are created in migrate_db() so the column-add migrations and
    # index creation happen in the right order on legacy DBs.

    conn.commit()
    migrate_db(conn)
    conn.close()
    print(f"Database initialized at {db_path or DB_PATH}")


def upsert_meeting(conn, rto, committee, title, meeting_date,
                   meeting_time=None, location=None,
                   source_url=None, detail_url=None, materials_url=None):
    """Insert or update a meeting record. Returns the meeting ID."""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO meetings (rto, committee, title, meeting_date,
                              meeting_time, location, source_url,
                              detail_url, materials_url, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(rto, title, meeting_date) DO UPDATE SET
            committee = COALESCE(excluded.committee, committee),
            meeting_time = COALESCE(excluded.meeting_time, meeting_time),
            location = COALESCE(excluded.location, location),
            source_url = COALESCE(excluded.source_url, source_url),
            detail_url = COALESCE(excluded.detail_url, detail_url),
            materials_url = COALESCE(excluded.materials_url, materials_url),
            updated_at = CURRENT_TIMESTAMP
    """, (rto, committee, title, meeting_date, meeting_time,
          location, source_url, detail_url, materials_url))
    conn.commit()

    row = cursor.execute(
        "SELECT id FROM meetings WHERE rto=? AND title=? AND meeting_date=?",
        (rto, title, meeting_date)
    ).fetchone()
    return row["id"]


def upsert_document(conn, meeting_id, rto, download_url,
                    doc_type=None, title=None, filename=None,
                    posted_date=None):
    """Insert or update a document record. Returns the document ID."""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO documents (meeting_id, rto, download_url, doc_type,
                               title, filename, posted_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(download_url) DO UPDATE SET
            doc_type = COALESCE(excluded.doc_type, doc_type),
            title = COALESCE(excluded.title, title),
            filename = COALESCE(excluded.filename, filename),
            posted_date = COALESCE(excluded.posted_date, posted_date)
    """, (meeting_id, rto, download_url, doc_type, title, filename,
          posted_date))
    conn.commit()

    row = cursor.execute(
        "SELECT id FROM documents WHERE download_url=?",
        (download_url,)
    ).fetchone()
    return row["id"]


def mark_downloaded(conn, doc_id, local_path, file_size=None,
                    content_type=None):
    """Mark a document as downloaded."""
    conn.execute("""
        UPDATE documents SET
            local_path = ?,
            file_size = ?,
            content_type = ?,
            downloaded_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (local_path, file_size, content_type, doc_id))
    conn.commit()


def log_scrape(conn, rto, scrape_type, target_url, status,
               events_found=0, docs_found=0, docs_downloaded=0,
               error_message=None, duration_seconds=None):
    """Log a scrape attempt."""
    conn.execute("""
        INSERT INTO scrape_log (rto, scrape_type, target_url, status,
                                events_found, docs_found, docs_downloaded,
                                error_message, duration_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (rto, scrape_type, target_url, status, events_found,
          docs_found, docs_downloaded, error_message, duration_seconds))
    conn.commit()


def migrate_db(conn):
    """Add any missing columns to existing databases (safe to run repeatedly)."""
    # Document-level columns
    doc_existing = {
        row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()
    }
    doc_additions = [
        ("extracted_text",            "TEXT"),
        ("extracted_at",              "TIMESTAMP"),
        ("hydro_relevant",            "INTEGER"),
        ("hydro_relevance_reason",    "TEXT"),
        ("ai_summary",                "TEXT"),
        ("ai_processed_at",           "TIMESTAMP"),
        ("stakeholders_extracted_at", "TIMESTAMP"),
    ]
    for col, col_type in doc_additions:
        if col not in doc_existing:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {col_type}")

    # Meeting-level screening columns
    mtg_existing = {
        row[1] for row in conn.execute("PRAGMA table_info(meetings)").fetchall()
    }
    mtg_additions = [
        ("hydro_relevant",        "INTEGER"),
        ("hydro_relevance_reason","TEXT"),
        ("meeting_screened_at",   "TIMESTAMP"),
    ]
    for col, col_type in mtg_additions:
        if col not in mtg_existing:
            conn.execute(f"ALTER TABLE meetings ADD COLUMN {col} {col_type}")

    # Tables added later — create on existing DBs that pre-date them.
    # Schema is the *current* shape; if a DB pre-dates a column added later,
    # the ALTER block below adds it before any index that depends on it.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rto TEXT NOT NULL,
            native_id TEXT NOT NULL,
            url TEXT,
            canonical_name TEXT,
            status TEXT,
            stakeholder_phase TEXT,
            committee_owner TEXT,
            committee_owner_label TEXT,
            is_open INTEGER,
            annual_plan_year INTEGER,
            initiated_date TEXT,
            work_begins_date TEXT,
            target_completion_date TEXT,
            actual_completion_date TEXT,
            facilitator TEXT,
            sme TEXT,
            short_title TEXT,
            phase INTEGER,
            eim_categories TEXT,
            stage_a TEXT,
            stage_b TEXT,
            stage_c TEXT,
            stage_d TEXT,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(rto, native_id)
        );
        CREATE TABLE IF NOT EXISTS issue_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            ref_url TEXT NOT NULL,
            ref_title TEXT,
            matched_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            matched_meeting_id INTEGER REFERENCES meetings(id) ON DELETE SET NULL,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(issue_id, ref_url)
        );
        CREATE TABLE IF NOT EXISTS caiso_event_cache (
            event_id TEXT PRIMARY KEY,
            url TEXT,
            title TEXT,
            start_date TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS document_stakeholders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            entity TEXT,
            role TEXT,
            email TEXT,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(document_id, name, entity)
        );
        CREATE INDEX IF NOT EXISTS idx_doc_stakeholders_doc
            ON document_stakeholders(document_id);
        CREATE INDEX IF NOT EXISTS idx_doc_stakeholders_entity
            ON document_stakeholders(entity);
    """)

    # Add columns that may be missing on a pre-existing DB. Must happen
    # BEFORE we create indexes that reference them.
    issue_existing = {
        row[1] for row in conn.execute("PRAGMA table_info(issues)").fetchall()
    }
    issue_additions = [
        ("short_title",     "TEXT"),
        ("phase",           "INTEGER"),
        ("eim_categories",  "TEXT"),
        ("stage_a",         "TEXT"),
        ("stage_b",         "TEXT"),
        ("stage_c",         "TEXT"),
        ("stage_d",         "TEXT"),
    ]
    for col, col_type in issue_additions:
        if col not in issue_existing:
            conn.execute(f"ALTER TABLE issues ADD COLUMN {col} {col_type}")

    ref_existing = {
        row[1] for row in conn.execute("PRAGMA table_info(issue_references)").fetchall()
    }
    if "matched_meeting_id" not in ref_existing:
        conn.execute("ALTER TABLE issue_references ADD COLUMN matched_meeting_id INTEGER")

    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_issues_rto_status
            ON issues(rto, status);
        CREATE INDEX IF NOT EXISTS idx_issue_refs_url
            ON issue_references(ref_url);
        CREATE INDEX IF NOT EXISTS idx_issue_refs_doc
            ON issue_references(matched_document_id);
        CREATE INDEX IF NOT EXISTS idx_issue_refs_meeting
            ON issue_references(matched_meeting_id);
    """)

    conn.commit()


def save_extracted_text(conn, doc_id, text):
    """Store extracted plain text for a document."""
    conn.execute("""
        UPDATE documents
        SET extracted_text = ?, extracted_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (text, doc_id))
    conn.commit()


def save_meeting_screening(conn, meeting_id, hydro_relevant, reason):
    """Store Stage 1 screening result for a meeting."""
    conn.execute("""
        UPDATE meetings
        SET hydro_relevant = ?,
            hydro_relevance_reason = ?,
            meeting_screened_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (1 if hydro_relevant else 0, reason, meeting_id))
    conn.commit()


def save_ai_screening(conn, doc_id, hydro_relevant, reason, summary=None):
    """Store Haiku screening result for a document."""
    conn.execute("""
        UPDATE documents
        SET hydro_relevant = ?,
            hydro_relevance_reason = ?,
            ai_summary = COALESCE(?, ai_summary),
            ai_processed_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (1 if hydro_relevant else 0, reason, summary, doc_id))
    conn.commit()


def upsert_issue(conn, rto, native_id, **fields):
    """
    Insert or update an issue. Returns the issue ID.

    `fields` may contain any column from the issues table other than
    rto, native_id, id, first_seen_at. None values are coalesced so a
    partial update doesn't blank out previously-stored data.
    """
    allowed = {
        "url", "canonical_name", "status", "stakeholder_phase",
        "committee_owner", "committee_owner_label",
        "is_open", "annual_plan_year",
        "initiated_date", "work_begins_date",
        "target_completion_date", "actual_completion_date",
        "facilitator", "sme",
        "short_title", "phase", "eim_categories",
        "stage_a", "stage_b", "stage_c", "stage_d",
    }
    cols, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            cols.append(k)
            vals.append(v)

    insert_cols = ["rto", "native_id"] + cols
    placeholders = ",".join("?" * len(insert_cols))
    update_set = ",\n            ".join(
        f"{c} = COALESCE(excluded.{c}, {c})" for c in cols
    )
    sql = f"""
        INSERT INTO issues ({",".join(insert_cols)})
        VALUES ({placeholders})
        ON CONFLICT(rto, native_id) DO UPDATE SET
            {update_set + ',' if update_set else ''}
            last_seen_at = CURRENT_TIMESTAMP
    """
    conn.execute(sql, [rto, native_id] + vals)
    conn.commit()

    row = conn.execute(
        "SELECT id FROM issues WHERE rto=? AND native_id=?",
        (rto, native_id),
    ).fetchone()
    return row["id"]


def upsert_issue_reference(conn, issue_id, ref_url, ref_title=None):
    """Insert or update an issue→URL reference. Returns the row ID."""
    conn.execute("""
        INSERT INTO issue_references (issue_id, ref_url, ref_title)
        VALUES (?, ?, ?)
        ON CONFLICT(issue_id, ref_url) DO UPDATE SET
            ref_title = COALESCE(excluded.ref_title, ref_title),
            last_seen_at = CURRENT_TIMESTAMP
    """, (issue_id, ref_url, ref_title))
    conn.commit()
    row = conn.execute(
        "SELECT id FROM issue_references WHERE issue_id=? AND ref_url=?",
        (issue_id, ref_url),
    ).fetchone()
    return row["id"]


def resolve_issue_references(conn):
    """
    Match issue_references.ref_url against both documents.download_url and
    meetings.detail_url, populating matched_document_id and matched_meeting_id
    respectively. Returns dict with counts.

    PJM cites individual document URLs, so its references resolve via the
    documents path. CAISO cites per-meeting calendar URLs, so its references
    resolve via the meetings path. The same reference table handles both.

    Idempotent: re-running picks up newly-arrived rows on either side.
    """
    conn.execute("""
        UPDATE issue_references
        SET matched_document_id = (
            SELECT d.id FROM documents d
            WHERE d.download_url = issue_references.ref_url
            LIMIT 1
        )
        WHERE matched_document_id IS NULL
    """)
    conn.execute("""
        UPDATE issue_references
        SET matched_meeting_id = (
            SELECT m.id FROM meetings m
            WHERE m.detail_url = issue_references.ref_url
            LIMIT 1
        )
        WHERE matched_meeting_id IS NULL
    """)
    conn.commit()
    doc_matched = conn.execute(
        "SELECT COUNT(*) c FROM issue_references WHERE matched_document_id IS NOT NULL"
    ).fetchone()["c"]
    mtg_matched = conn.execute(
        "SELECT COUNT(*) c FROM issue_references WHERE matched_meeting_id IS NOT NULL"
    ).fetchone()["c"]
    unmatched = conn.execute(
        "SELECT COUNT(*) c FROM issue_references "
        "WHERE matched_document_id IS NULL AND matched_meeting_id IS NULL"
    ).fetchone()["c"]
    return {"doc_matched": doc_matched, "meeting_matched": mtg_matched, "unmatched": unmatched}


def save_document_stakeholders(conn, doc_id, stakeholders, source_text=None):
    """
    Replace the stakeholder rows for a document with the provided list.

    `stakeholders` is a list of dicts with name/entity/role/email keys.
    Each `email` is verified against `source_text` (case-insensitive
    substring) before being stored — Haiku occasionally fabricates a
    plausible-looking address when given a name + employer, so we drop
    any email that isn't actually present in the document.

    Always sets `documents.stakeholders_extracted_at` so the gate logic
    in screen_documents.py knows we've processed this doc, even if no
    stakeholders were found.
    """
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM document_stakeholders WHERE document_id = ?",
        (doc_id,),
    )

    haystack = (source_text or "").lower()
    for s in (stakeholders or []):
        name = (s.get("name") or "").strip()
        if not name:
            continue
        entity = (s.get("entity") or "").strip() or None
        role = (s.get("role") or "").strip().lower() or None
        email = (s.get("email") or "").strip() or None
        # Verify the email substring actually appears in the doc text;
        # otherwise treat it as a hallucination and drop just the email.
        if email and (not haystack or email.lower() not in haystack):
            email = None
        try:
            cursor.execute("""
                INSERT INTO document_stakeholders (document_id, name, entity, role, email)
                VALUES (?, ?, ?, ?, ?)
            """, (doc_id, name, entity, role, email))
        except sqlite3.IntegrityError:
            # Same (doc, name, entity) combo from a duplicated record;
            # silently skip rather than crash the screening pass.
            pass

    cursor.execute(
        "UPDATE documents SET stakeholders_extracted_at = CURRENT_TIMESTAMP WHERE id = ?",
        (doc_id,),
    )
    conn.commit()


def cache_caiso_event(conn, event_id, url, title=None, start_date=None):
    """Stash a resolved CAISO event-id → url mapping so we don't re-fetch."""
    conn.execute("""
        INSERT INTO caiso_event_cache (event_id, url, title, start_date)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
            url = excluded.url,
            title = COALESCE(excluded.title, title),
            start_date = COALESCE(excluded.start_date, start_date)
    """, (event_id, url, title, start_date))
    conn.commit()


def get_cached_caiso_event(conn, event_id):
    """Return cached row for an event id, or None."""
    return conn.execute(
        "SELECT url, title, start_date FROM caiso_event_cache WHERE event_id = ?",
        (event_id,),
    ).fetchone()


def get_stats(conn):
    """Get summary statistics."""
    stats = {}
    for rto_row in conn.execute(
        "SELECT DISTINCT rto FROM meetings ORDER BY rto"
    ).fetchall():
        rto = rto_row["rto"]
        meetings = conn.execute(
            "SELECT COUNT(*) as c FROM meetings WHERE rto=?", (rto,)
        ).fetchone()["c"]
        docs = conn.execute(
            "SELECT COUNT(*) as c FROM documents WHERE rto=?", (rto,)
        ).fetchone()["c"]
        downloaded = conn.execute(
            "SELECT COUNT(*) as c FROM documents WHERE rto=? AND downloaded_at IS NOT NULL",
            (rto,)
        ).fetchone()["c"]
        stats[rto] = {
            "meetings": meetings,
            "documents": docs,
            "downloaded": downloaded,
        }
    return stats


def export_calendar_json(conn, output_path=None):
    """Export meetings + documents as JSON for the web calendar."""
    rows = conn.execute("""
        SELECT m.*, GROUP_CONCAT(d.id) as doc_ids
        FROM meetings m
        LEFT JOIN documents d ON d.meeting_id = m.id
        GROUP BY m.id
        ORDER BY m.meeting_date DESC
    """).fetchall()

    events = []
    for row in rows:
        # Initiatives linked at the meeting level (CAISO uses this path).
        meeting_issues = conn.execute("""
            SELECT i.rto, i.native_id, i.canonical_name,
                   i.status, i.stakeholder_phase,
                   i.committee_owner, i.is_open, i.url
            FROM issue_references ir
            JOIN issues i ON i.id = ir.issue_id
            WHERE ir.matched_meeting_id = ?
            ORDER BY i.canonical_name
        """, (row["id"],)).fetchall()

        event = {
            "title": row["title"],
            "date": row["meeting_date"],
            "time": row["meeting_time"],
            "rto": row["rto"],
            "committee": row["committee"],
            "source_url": row["source_url"],
            "detail_url": row["detail_url"],
            "materials_url": row["materials_url"],
            "meeting_hydro_relevant": bool(row["hydro_relevant"]) if row["hydro_relevant"] is not None else None,
            "meeting_hydro_reason": row["hydro_relevance_reason"],
            "issues": [dict(r) for r in meeting_issues],
            "documents": [],
        }

        if row["doc_ids"]:
            doc_ids = row["doc_ids"].split(",")
            for did in doc_ids:
                doc = conn.execute(
                    "SELECT * FROM documents WHERE id=?", (did,)
                ).fetchone()
                if doc:
                    issue_rows = conn.execute("""
                        SELECT i.rto, i.native_id, i.canonical_name,
                               i.status, i.stakeholder_phase,
                               i.committee_owner, i.is_open, i.url
                        FROM issue_references ir
                        JOIN issues i ON i.id = ir.issue_id
                        WHERE ir.matched_document_id = ?
                        ORDER BY i.canonical_name
                    """, (doc["id"],)).fetchall()
                    stakeholder_rows = conn.execute("""
                        SELECT name, entity, role, email
                        FROM document_stakeholders
                        WHERE document_id = ?
                        ORDER BY entity, name
                    """, (doc["id"],)).fetchall()
                    event["documents"].append({
                        "type": doc["doc_type"],
                        "title": doc["title"],
                        "filename": doc["filename"],
                        "url": doc["download_url"],
                        "posted_date": doc["posted_date"],
                        "hydro_relevant": bool(doc["hydro_relevant"]) if doc["hydro_relevant"] is not None else None,
                        "hydro_relevance_reason": doc["hydro_relevance_reason"],
                        "ai_summary": doc["ai_summary"],
                        "issues": [dict(r) for r in issue_rows],
                        "stakeholders": [dict(r) for r in stakeholder_rows],
                    })

        events.append(event)

    if output_path:
        with open(output_path, "w") as f:
            json.dump(events, f, indent=2)
        print(f"Exported {len(events)} events to {output_path}")

    return events


def export_issues_json(conn, output_path=None):
    """Export issues with reference counts as JSON for the web UI."""
    rows = conn.execute("""
        SELECT i.*,
               (SELECT COUNT(*) FROM issue_references ir
                WHERE ir.issue_id = i.id) AS total_refs,
               (SELECT COUNT(*) FROM issue_references ir
                WHERE ir.issue_id = i.id
                  AND ir.matched_document_id IS NOT NULL) AS matched_refs
        FROM issues i
        ORDER BY i.is_open DESC, i.rto, i.canonical_name
    """).fetchall()

    issues = [dict(r) for r in rows]
    for issue in issues:
        issue["is_open"] = bool(issue["is_open"]) if issue["is_open"] is not None else None

    if output_path:
        with open(output_path, "w") as f:
            json.dump(issues, f, indent=2)
        print(f"Exported {len(issues)} issues to {output_path}")

    return issues


if __name__ == "__main__":
    init_db()
