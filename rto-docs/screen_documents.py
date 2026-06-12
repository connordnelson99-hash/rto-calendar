#!/usr/bin/env python3
"""
Two-stage AI relevance screening for RTO meetings and documents.

Stage 1 — Meeting screening (cheap, title/committee only):
    Claude screens each meeting's title + committee to decide if the meeting
    is worth looking at for hydropower. No document downloads needed.

Stage 2 — Document screening (targeted, larger text window):
    For meetings that passed Stage 1, Claude screens each document using its
    title plus up to 8,000 characters of extracted text.

Usage:
    python screen_documents.py                   # run both stages
    python screen_documents.py --stage 1         # meeting-level only
    python screen_documents.py --stage 2         # doc-level only (meetings pre-screened)
    python screen_documents.py --rto PJM         # filter to one RTO
    python screen_documents.py --rescreen        # re-screen already-processed items
    python screen_documents.py --limit 200       # cap docs screened in stage 2
    python screen_documents.py --dry-run         # print prompts without calling API
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Load .env for local runs. override=True so a pre-set-but-empty
# ANTHROPIC_API_KEY in the parent shell can't shadow the .env value.
# CI sets the key directly via repo secret, so the missing-file branch
# is fine — load_dotenv() is a no-op when no file is present.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))
from db.database import (
    get_connection, init_db,
    save_meeting_screening, save_ai_screening,
    save_document_stakeholders,
)

# ── Shared system prompt ────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an analyst for the National Hydropower Association (NHA), screening
RTO/ISO/FERC meeting materials for NHA's Market Design Committee — owners and
operators of conventional hydropower and pumped-storage hydro (PSH). They read
this feed to learn which market-design developments affect their revenue,
their compliance obligations, or their strategic options. Screen for THEIR
interests, not for generic energy-industry news.

A document is RELEVANT when a hydro or PSH owner reading it would learn
something that touches one of these:

1. REVENUE — how hydro's products are priced, procured, or accredited:
   - Energy price formation: scarcity pricing, uplift/make-whole, price
     adders, locational signals, real-time co-optimization
   - Ancillary services hydro typically supplies: regulation, spinning/
     operating reserves, fast frequency response, inertia, reactive power/
     voltage support, black start
   - Capacity markets and accreditation: ELCC/UCAP for hydro and storage,
     seasonal accreditation, performance assessments and penalties
   - Storage participation models — pumped storage especially (charging
     energy treatment, state-of-charge rules, AS stacking, long-duration
     storage programs), plus battery rules that set precedent for PSH
   - Hybrid / co-located resource rules
   - Energy attribute certificates, RECs, clean-energy program design

2. OBLIGATIONS — rules a hydro operator must comply with or respond to:
   - Interconnection and deliverability rules (incl. FERC Order 2023-era
     queue reform), must-offer requirements, outage scheduling
   - Operating standards reaching synchronous units: ride-through,
     winterization/resource readiness, dam-adjacent reliability standards
   - Metering, settlement, and dispatch-instruction changes

3. STRATEGIC OPTIONS — developments that change what hydro owners can do:
   - Resource adequacy constructs and reliability mechanisms
   - Transmission planning/expansion affecting hydro deliverability
   - Water, drought, and river-operations issues as they intersect markets
   - Licensing/relicensing or environmental compliance touching markets
   - Market seams and governance: EDAM/WEIM, Markets+, RTO membership
     choices — NHA members in the West are actively choosing between these
   - FERC orders and tariff filings in active compliance phases

NOT relevant: retail-market mechanics (customer switching, billing, data
transport, MarkeTrak/Texas SET), routine IT and operations reports, training
logistics, credit/settlement administration, corporate governance unrelated
to market rules, and fuel-specific issues with no hydro analog (e.g. gas
pipeline coordination specifics) UNLESS the rule's design would extend to
dispatchable or storage resources generally.

Calibration: prefer recall. This screen feeds a weekly digest that triages
aggressively, so a borderline-relevant document should be flagged relevant —
a false positive costs a reader seconds, a false negative never reaches
members. Reserve relevant=false for documents you are confident carry nothing
on the axes above.

Special case — ERCOT: Texas has essentially no hydropower, but NHA tracks
ERCOT for cross-market comparison (e.g. how ERCOT handles frequency response
or storage participation differently from hydro-rich markets like NYISO).
Flag ERCOT content as relevant when it addresses market-design topics hydro
cares about elsewhere — ancillary services and frequency response, energy
storage participation, capacity/reliability mechanisms, price formation,
flexibility products — even though no hydro fleet is directly affected, and
frame its summary as a comparison point. ERCOT retail-market, IT, and admin
content remains not relevant.
"""

# ── Stage 1: Meeting screening prompt ──────────────────────────────────────

MEETING_PROMPT = """\
Evaluate whether this RTO/ISO meeting is likely to contain content relevant
to the hydropower and pumped-storage industry based on its title and committee.

Meeting:
  RTO: {rto}
  Committee: {committee}
  Title: {title}
  Date: {meeting_date}

Answer in exactly this JSON format (no other text):
{{
  "relevant": true or false,
  "reason": "one sentence explaining why or why not"
}}
"""

# ── Stage 2: Document screening prompt ─────────────────────────────────────

DOCUMENT_PROMPT = """\
Evaluate whether this RTO/ISO document contains content relevant to the
hydropower and pumped-storage industry, and identify the named stakeholders
who authored or are listed as contacts on it.

Document metadata:
  RTO: {rto}
  Committee: {committee}
  Meeting date: {meeting_date}
  Meeting title: {meeting_title}
  Document title: {doc_title}
  Document type: {doc_type}

How to read the document text below — it is MACHINE-EXTRACTED, not the
original layout:
- Tables appear as pipe-delimited rows: | cell | cell | cell |. Within a
  pipe row, position is meaningful — read it like a table row. In PDFs,
  table content may appear twice: flattened in the prose flow AND as pipe
  rows. The pipe rows are the authoritative reading for which value
  belongs to which row/column; the flattened prose ordering is unreliable.
- "[Page N]" / "[Slide N]" markers separate pages/slides. Content under
  different markers belongs to different pages — don't merge across them.
- Chart data appears as: Chart "series name": label=value; label=value.
- Extraction is imperfect: stray numbers may appear without context
  (chart annotations, footers), and prose order can scramble on dense
  layouts. If you cannot tell with confidence which row, column, series,
  or resource a number belongs to, DO NOT attribute it in your summary —
  describe the finding qualitatively or omit the figure. A summary that
  misattributes a number is worse than one with no number.

Document text (excerpt):
{text_excerpt}

---
Answer in exactly this JSON format (no other text):
{{
  "relevant": true or false,
  "reason": "one sentence explaining why or why not",
  "summary": "if relevant, 2-3 sentences (see rules below); otherwise null",
  "stakeholders": [
    {{
      "name": "<full name as it appears>",
      "entity": "<company/org/agency they represent, e.g. Constellation, NRG, PJM>",
      "role": "<author | co-author | contact | presenter | signatory | sponsor>",
      "email": "<email address ONLY if it appears verbatim in the text; otherwise null>"
    }}
  ]
}}

Summary rules (these summaries go directly to hydro asset owners):
- Lead with the substance: what is proposed/decided/reported, and the hook
  for hydro or pumped storage. Don't open with "This document..." — open
  with the thing itself.
- Include status and next step when stated (e.g. "tabled at PRS",
  "board vote scheduled"), and quote any comment deadline or vote date
  VERBATIM — deadlines are the single most actionable thing in this feed.
- State figures only when their attribution is unambiguous in the text
  (see extraction notes above). Never reconstruct or estimate a value.
- Name the position-takers when the doc states positions ("ERCOT opposes",
  "LCRA's comments support...").

Stakeholder extraction rules:
- Include named individuals from cover pages, "submitted by" lines, "contact:" blocks,
  signature blocks, author lists, and presenter credits.
- Prefer external stakeholders (utilities, advocacy groups, trade associations,
  consultancies) over RTO/ISO staff, but include both.
- Do NOT invent or guess email addresses. If an address isn't shown, set email to null.
- Do NOT include people merely mentioned in passing (e.g. names cited in a footnote).
- Return an empty array [] if no contributors are identifiable.
"""

# 12K chars ≈ 3K tokens of Haiku input (~$0.003/doc) — worth it: RTO docs
# front-load boilerplate (NPRR forms, revision-history tables), and the
# structured table/slide markers consume some of the budget.
MAX_DOC_CHARS = 12000


# ── API helpers ─────────────────────────────────────────────────────────────

def _call_claude(client, prompt, max_tokens=256):
    """Call Claude and parse JSON response. Returns raw dict."""
    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw)


def screen_meeting(client, meeting_row, dry_run=False):
    """
    Stage 1: Screen a meeting by title + committee.
    Returns (relevant: bool, reason: str).
    """
    prompt = MEETING_PROMPT.format(
        rto=meeting_row["rto"] or "",
        committee=meeting_row["committee"] or "",
        title=meeting_row["title"] or "",
        meeting_date=meeting_row["meeting_date"] or "",
    )

    if dry_run:
        print(f"\n--- DRY RUN (meeting {meeting_row['id']}) ---")
        print(prompt)
        return True, "dry-run"

    try:
        result = _call_claude(client, prompt, max_tokens=128)
        return bool(result.get("relevant", False)), result.get("reason", "")
    except json.JSONDecodeError as e:
        return False, f"parse error: {e}"


def screen_document(client, doc_row, dry_run=False):
    """
    Stage 2: Screen a document by title + text excerpt.
    Returns (relevant: bool, reason: str, summary: str|None, stakeholders: list).
    """
    text = doc_row["extracted_text"] or ""
    excerpt = text[:MAX_DOC_CHARS].strip()

    prompt = DOCUMENT_PROMPT.format(
        rto=doc_row["rto"] or "",
        committee=doc_row["committee"] or "",
        meeting_date=doc_row["meeting_date"] or "",
        meeting_title=doc_row["meeting_title"] or "",
        doc_title=doc_row["title"] or doc_row["filename"] or "",
        doc_type=doc_row["doc_type"] or "",
        text_excerpt=excerpt or "(no text extracted — screening title only)",
    )

    if dry_run:
        print(f"\n--- DRY RUN (doc {doc_row['id']}) ---")
        print(prompt[:600], "...")
        return True, "dry-run", None, []

    try:
        # Larger budget than the old 384 to fit the new stakeholders array.
        # Most docs have 0-3 stakeholders; a few PJM matrices list 10+.
        result = _call_claude(client, prompt, max_tokens=900)
        stakeholders = result.get("stakeholders") or []
        if not isinstance(stakeholders, list):
            stakeholders = []
        return (
            bool(result.get("relevant", False)),
            result.get("reason", ""),
            result.get("summary"),
            stakeholders,
        )
    except json.JSONDecodeError as e:
        return False, f"parse error: {e}", None, []


# ── Stage runners ────────────────────────────────────────────────────────────

def run_stage1(conn, client, rto_filter=None, rescreen=False, dry_run=False):
    """Screen meetings by title/committee. Returns count of relevant meetings."""
    where = []
    params = []

    if not rescreen:
        where.append("meeting_screened_at IS NULL")
    if rto_filter:
        where.append("rto = ?")
        params.append(rto_filter.upper())

    sql = "SELECT id, rto, committee, title, meeting_date FROM meetings"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY meeting_date DESC"

    meetings = conn.execute(sql, params).fetchall()
    print(f"\n{'='*60}")
    print(f"  Stage 1: Meeting Screening")
    print(f"{'='*60}")
    print(f"  {len(meetings)} meetings to screen\n")

    if not meetings:
        print("  Nothing to screen.")
        return 0

    relevant_count = 0
    for i, m in enumerate(meetings, 1):
        label = f"{m['rto']} | {(m['committee'] or '').strip()} | {(m['title'] or '')[:50]}"
        print(f"  [{i}/{len(meetings)}] {label}", end=" ... ", flush=True)

        try:
            relevant, reason = screen_meeting(client, m, dry_run)
            save_meeting_screening(conn, m["id"], relevant, reason)
            flag = "YES" if relevant else "no"
            print(f"{flag} — {reason[:80]}")
            if relevant:
                relevant_count += 1
        except Exception as e:
            print(f"ERROR: {e}")

    print(f"\n  Stage 1 complete: {relevant_count}/{len(meetings)} meetings flagged as relevant")
    return relevant_count


def run_stage2(conn, client, rto_filter=None, rescreen=False, limit=200, dry_run=False):
    """Screen documents for meetings that passed Stage 1."""
    where = [
        # Stage-1 gate: only docs from meetings flagged relevant — EXCEPT
        # NYISO, SPP, MISO, and ERCOT. Their meeting titles are just the
        # committee name / acronym (the agenda lives only inside the agenda
        # doc), so Stage 1 has too little signal and filters out
        # broad-but-important venues like NYISO's Business Issues Committee,
        # SPP's Markets+ working groups, MISO's Planning Advisory Committee,
        # or ERCOT's WMS/PRS. Screening every doc on its own extracted text
        # recovers the relevant material those meetings carry.
        "(m.hydro_relevant = 1 OR d.rto IN "
        "('NYISO', 'SPP', 'SPP Markets +', 'MISO', 'ERCOT'))",
    ]
    params = []

    if not rescreen:
        # Re-run if either gate is unset. Existing pre-stakeholder docs
        # have ai_processed_at set but stakeholders_extracted_at IS NULL,
        # so this naturally backfills the stakeholder column on next run.
        where.append("(d.ai_processed_at IS NULL OR d.stakeholders_extracted_at IS NULL)")
    if rto_filter:
        where.append("d.rto = ?")
        params.append(rto_filter.upper())

    docs = conn.execute(f"""
        SELECT d.id, d.rto, d.doc_type, d.title, d.filename,
               d.extracted_text,
               m.id as meeting_id, m.committee, m.meeting_date, m.title as meeting_title
        FROM documents d
        JOIN meetings m ON m.id = d.meeting_id
        WHERE {" AND ".join(where)}
        ORDER BY m.meeting_date DESC
        LIMIT ?
    """, params + [limit]).fetchall()

    print(f"\n{'='*60}")
    print(f"  Stage 2: Document Screening")
    print(f"{'='*60}")
    print(f"  {len(docs)} documents to screen (from relevant meetings)\n")

    if not docs:
        print("  Nothing to screen.")
        print("  Tip: Run Stage 1 first if you haven't yet (--stage 1)")
        return 0, 0

    relevant_count = 0
    error_count = 0
    stakeholder_count = 0

    for i, doc in enumerate(docs, 1):
        label = (doc["title"] or doc["filename"] or "untitled")[:60]
        has_text = bool(doc["extracted_text"])
        text_note = f"{len(doc['extracted_text'] or '')} chars" if has_text else "no text"
        print(f"  [{i}/{len(docs)}] {doc['rto']} | {label} ({text_note})", end=" ... ", flush=True)

        try:
            relevant, reason, summary, stakeholders = screen_document(client, doc, dry_run)
            save_ai_screening(conn, doc["id"], relevant, reason, summary)
            save_document_stakeholders(
                conn, doc["id"], stakeholders, source_text=doc["extracted_text"]
            )
            stakeholder_count += len(stakeholders)
            flag = "YES" if relevant else "no"
            extras = f", {len(stakeholders)} stakeholders" if stakeholders else ""
            print(f"{flag}{extras} — {reason[:80]}")
            if relevant:
                relevant_count += 1
        except Exception as e:
            print(f"ERROR: {e}")
            error_count += 1

    print(f"\n  Stage 2 complete: {relevant_count}/{len(docs)} documents flagged as relevant; "
          f"{stakeholder_count} stakeholders extracted")
    return relevant_count, error_count


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Two-stage screening of RTO meetings and documents for hydro relevance"
    )
    parser.add_argument(
        "--stage", type=int, choices=[1, 2],
        help="Run only Stage 1 (meetings) or Stage 2 (documents). Default: both."
    )
    parser.add_argument("--rto", help="Filter to one RTO (e.g. PJM, CAISO)")
    parser.add_argument(
        "--rescreen", action="store_true",
        help="Re-screen items that were already processed"
    )
    parser.add_argument(
        "--limit", type=int, default=200,
        help="Max documents to screen in Stage 2 per run (default: 200)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print prompts without calling the API"
    )
    args = parser.parse_args()

    init_db()
    conn = get_connection()

    # Initialise Anthropic client
    if not args.dry_run:
        # Fail loudly if the key is missing — the SDK happily constructs a
        # client with no key and only errors on first request, which gets
        # caught by per-row exception handlers and exits 0 (green CI, no
        # screening done). Don't let that happen again.
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key.strip():
            print("ERROR: ANTHROPIC_API_KEY is missing or empty.")
            print("Set it in rto-docs/.env (local) or as a repo secret (CI).")
            sys.exit(1)

        try:
            import anthropic
            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        except ImportError:
            print("ERROR: anthropic package not installed.")
            print("Run: .venv/Scripts/pip install anthropic")
            sys.exit(1)
        except Exception as e:
            print(f"ERROR initialising Anthropic client: {e}")
            sys.exit(1)
    else:
        client = None

    run_s1 = args.stage in (None, 1)
    run_s2 = args.stage in (None, 2)

    if run_s1:
        run_stage1(conn, client,
                   rto_filter=args.rto,
                   rescreen=args.rescreen,
                   dry_run=args.dry_run)

    if run_s2:
        run_stage2(conn, client,
                   rto_filter=args.rto,
                   rescreen=args.rescreen,
                   limit=args.limit,
                   dry_run=args.dry_run)

    conn.close()
    print("\nDone. Run: python run_scrapers.py --export-only  to refresh the calendar JSON.")


if __name__ == "__main__":
    main()
