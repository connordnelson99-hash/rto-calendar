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
        ("extracted_text",        "TEXT"),
        ("extracted_at",          "TIMESTAMP"),
        ("hydro_relevant",        "INTEGER"),
        ("hydro_relevance_reason","TEXT"),
        ("ai_summary",            "TEXT"),
        ("ai_processed_at",       "TIMESTAMP"),
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
            "documents": [],
        }

        if row["doc_ids"]:
            doc_ids = row["doc_ids"].split(",")
            for did in doc_ids:
                doc = conn.execute(
                    "SELECT * FROM documents WHERE id=?", (did,)
                ).fetchone()
                if doc:
                    event["documents"].append({
                        "type": doc["doc_type"],
                        "title": doc["title"],
                        "filename": doc["filename"],
                        "url": doc["download_url"],
                        "local_path": doc["local_path"],
                        "posted_date": doc["posted_date"],
                        "hydro_relevant": bool(doc["hydro_relevant"]) if doc["hydro_relevant"] is not None else None,
                        "hydro_relevance_reason": doc["hydro_relevance_reason"],
                        "ai_summary": doc["ai_summary"],
                    })

        events.append(event)

    if output_path:
        with open(output_path, "w") as f:
            json.dump(events, f, indent=2)
        print(f"Exported {len(events)} events to {output_path}")

    return events


if __name__ == "__main__":
    init_db()
