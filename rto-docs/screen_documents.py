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
import re
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
   - Ramping/uncertainty products (flexible ramping, ramp capability,
     AS demand curves) — hydro is the classic supplier of flexibility
   - Capacity markets and accreditation: ELCC/UCAP for hydro and storage,
     seasonal accreditation, performance assessments and penalties,
     auction parameters (CONE, demand-curve resets)
   - Market power mitigation as applied to use-limited resources:
     reference levels, default energy bids, opportunity-cost offer
     development, mitigation of storage — hydro is the canonical
     use-limited resource and chronically mis-fitted by these rules
   - Storage participation models — pumped storage especially (charging
     energy treatment, state-of-charge rules, AS stacking, long-duration
     storage programs), plus battery rules that set precedent for PSH
   - Hybrid / co-located resource rules; DER aggregation (Order 2222)
     as a participation pathway for small hydro
   - Treatment of run-of-river/variable hydro: intermittent-resource
     programs, forecasting requirements, forecast-error settlement
   - Energy attribute certificates, RECs, clean-energy program design,
     and GHG attribution/accounting in market footprints (WEIM/EDAM GHG
     attribution matters enormously to Northwest hydro exports)

2. OBLIGATIONS — rules a hydro operator must comply with or respond to:
   - Interconnection and deliverability rules (incl. FERC Order 2023-era
     queue reform), surplus interconnection service and uprates at
     existing plants, must-offer requirements, outage scheduling
   - Operating standards reaching synchronous units: ride-through,
     winterization/resource readiness, dam-adjacent reliability standards
   - Metering, settlement, and dispatch-instruction changes

3. STRATEGIC OPTIONS — developments that change what hydro owners can do:
   - Load growth, large-load interconnection, data centers, and
     co-located load — scarcity from load growth raises the value of
     every existing hydro MW, and co-location rules may let hydro serve
     load behind the meter; treat these as squarely relevant
   - Resource adequacy constructs and reliability mechanisms; seasonal
     energy-adequacy studies (incl. hydro-conditions outlooks)
   - Transmission planning/expansion affecting hydro deliverability,
     interregional transfer capability, storage-as-transmission and
     other non-wires alternatives
   - RMR/mothball/retirement processes (fleet decisions and scarcity)
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

Calibration: prefer recall — this screen is the single gate for THREE
downstream consumers with different bars, and a document you reject is
permanently invisible to all of them:
1. A weekly member digest (triages aggressively — false positives cost it
   nothing).
2. The calendar UI, where members browse per-meeting documents.
3. An all-time analytical corpus used for ad-hoc cross-market analysis
   ("which RTOs are rethinking frequency response?"). This consumer values
   reference material the digest would skip: market-monitor reports,
   white papers, long-run RA/transmission studies, post-event analyses —
   keep these when they touch the axes above, even with no immediate
   member action in them.
Flag relevant if ANY of the three would plausibly want the document.
Reserve relevant=false for documents you are confident carry nothing on
the axes above. Write the summary to serve all three: self-contained
(name the RTO, mechanism, and proceeding), substantive enough to be
useful standing alone in a corpus search months later.

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

# ── Topic taxonomy ──────────────────────────────────────────────────────────
# Controlled vocabulary for per-document topic tags. These drive the
# calendar UI's topic filter and deterministic corpus pivots, so values
# must come from THIS list exactly — never free-form. Keep in sync with
# TOPIC_META in webcal-v2/data.js (labels live there).

TOPIC_TAGS = {
    "price-formation":    "energy pricing, scarcity pricing, uplift/make-whole, price adders, co-optimization",
    "ancillary-services": "reserves, regulation, frequency response, inertia, black start, voltage support",
    "ramping-flexibility": "ramping/uncertainty products, flexibility procurement, AS demand curves",
    "capacity-ra":        "capacity markets, accreditation/ELCC, resource adequacy, adequacy studies, auction parameters",
    "storage":            "storage participation, pumped storage, state-of-charge rules, LDES, storage-as-transmission",
    "hybrids-der":        "hybrid/co-located generation, DER aggregation, Order 2222",
    "mitigation-offers":  "market power mitigation, reference levels, opportunity-cost offers",
    "interconnection":    "interconnection queues, queue reform, surplus interconnection, uprates, deliverability",
    "transmission":       "transmission planning/expansion, interregional transfer, non-wires alternatives",
    "load-growth":        "large loads, data centers, co-located load, demand forecasting",
    "ops-compliance":     "ride-through, winterization, outage scheduling, must-offer, metering/settlement",
    "seams-governance":   "EDAM/WEIM, Markets+, market seams, RTO membership and governance",
    "water-hydrology":    "drought, river operations, hydro conditions, licensing/environmental",
    "clean-energy":       "RECs/EACs, GHG attribution and accounting, clean-energy program design",
    "ferc-policy":        "FERC orders, compliance filings, tariff changes",
}

_TOPIC_LIST_FOR_PROMPT = "\n".join(
    f"  {tag} — {desc}" for tag, desc in TOPIC_TAGS.items())

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
{elision_note}
Document text (excerpt):
{text_excerpt}

---
Answer in exactly this JSON format (no other text):
{{
  "relevant": true or false,
  "reason": "one sentence naming the MECHANISM: which hydro/PSH revenue stream, obligation, or strategic option this touches and how (or, if not relevant, why nothing applies)",
  "directness": "direct | precedent | reference — see the directness list below; null if not relevant",
  "summary": "if relevant, 2-4 sentences describing ONLY what this document says (see rules below); otherwise null",
  "hydro_read_through": "if relevant, 1-3 sentences on why a hydro/PSH owner should care — YOUR inference, not the document's words (see rules below); otherwise null",
  "topics": ["1-3 tags from the topic list below; [] if not relevant"],
  "evidence": [
    {{
      "claim": "<the date, deadline, or figure as you stated it in the summary>",
      "quote": "<the verbatim span from the document text above that supports it, copied character-for-character>"
    }}
  ],
  "stakeholders": [
    {{
      "name": "<full name as it appears>",
      "entity": "<company/org/agency they represent, e.g. Constellation, NRG, PJM>",
      "role": "<author | co-author | contact | presenter | signatory | sponsor>",
      "email": "<email address ONLY if it appears verbatim in the text; otherwise null>"
    }}
  ]
}}

Two fields, two jobs. `summary` reports the DOCUMENT. `hydro_read_through`
makes the hydro ARGUMENT. Keeping them apart is the point: a reader has to be
able to tell what the document establishes from what you inferred about it.
Never merge them, and never let the argument leak into `summary`.

Summary rules — `summary` describes the document and nothing else:
- Two moves:
  1. WHAT: the proposal/decision/finding, plus its status and next step
     when stated (e.g. "tabled at PRS", "board vote scheduled").
  2. WHO/WHEN: who is affected AS THE DOCUMENT ITSELF DESCRIBES THEM, and
     any comment deadline or vote date quoted VERBATIM — deadlines are the
     single most actionable thing in this feed.
- HARD RULE — never name a resource class the document does not name. If the
  document says "Non-Energy Limited Resources", write that; do NOT write
  "including most hydro plants", "hydro and PSH facilities", or "unlike
  batteries". If the document never mentions pumped storage, no sentence in
  `summary` may mention pumped storage. This holds even when the hydro
  read-through is obvious and correct — that argument goes in
  `hydro_read_through`, where it is labelled as inference.
- Never state a numeric threshold, exemption, assumed value, or
  per-technology treatment the document does not give.
- Don't open with "This document..." — open with the thing itself.
- State figures only when their attribution is unambiguous in the text
  (see extraction notes above). Never reconstruct or estimate a value.
- Name the position-takers when the doc states positions ("ERCOT opposes",
  "LCRA's comments support...").

Hydro read-through rules — `hydro_read_through` is where the hydro argument
belongs, and it IS wanted even when the connection is indirect. A document on
capacity-market reform that never says "hydro" still has real consequences for
hydro owners; explaining them is the job. What is forbidden is inventing
document detail to make that explanation land.
- 1-3 sentences: which hydro or PSH revenue stream, obligation, or strategic
  option this would touch, and in which direction. A category label is not a
  read-through — "relates to ancillary services hydro provides" is useless.
  Say what would actually change for an owner.
- Write it as reasoning, not as reportage. Arguing from a general rule to its
  hydro consequence is legitimate and wanted; phrasing that argument as
  something the document says is not.
- Reason from what the document establishes plus general market knowledge.
  Do NOT invent document-specific detail to support the argument: no
  thresholds, exemptions, carve-outs, or per-technology rules the document
  doesn't contain.
- When the source never names hydro, open by saying so, then make the
  argument. Worked example — an ISO-NE deck on accreditation process flows
  whose text only ever says "Non-Energy Limited Resources":
  "The deck never mentions hydro; it lays out the accreditation flow for
  non-energy-limited resources generally. Most conventional hydro is
  accredited through that path, so the DCap and rMRI steps shown here would
  set its seasonal capacity revenue — the mechanics worth watching are how
  median availability and the performance factor get measured. How pumped
  storage is handled isn't addressed here; ISO-NE lists energy-storage
  modeling as a separate open item."
  Note what that example does NOT claim: no battery exemption percentage, no
  proxy duration curve, no per-technology outage treatment. Those would be
  fabrications even though they sound like the kind of thing such a deck
  might say. If you catch yourself supplying a mechanism the document didn't,
  say instead that the document doesn't address it.
{hydro_mention_note}

Directness — how squarely this document bears on hydro. Relevance is a
deliberately wide gate; directness is how a reader knows whether a hit is
ABOUT hydro or merely adjacent to it, so rate it honestly rather than
generously. Use ONLY one of these exact values:
{directness_list}
Rules:
- Judge the DOCUMENT's own subject, not the committee's usual remit and not
  the strongest hydro angle you can construct.
- A rule of GENERAL applicability that hydro is itself subject to is
  "direct", even when the document never says "hydro" — ISO-NE capacity
  accreditation for "Non-Energy Limited Resources" governs conventional
  hydro whether or not it names it. Not naming hydro is a constraint on what
  `summary` may claim, not a reason to downgrade directness.
- A rule aimed at a DIFFERENT resource class is "precedent", not "direct",
  even when the read-through to PSH is obvious and important — a battery
  duration rule, or an ERCOT storage change, sets precedent for hydro
  rather than applying to it.
- If reaching hydro required a chain of inference beyond "hydro belongs to
  the category this document regulates", that is "precedent" at best.
- All ERCOT content is at most "precedent" — there is no Texas hydro fleet
  to affect directly.
- "reference" is not a demotion — market-monitor reports and white papers
  are exactly what the analytical corpus exists to hold. Use it freely.

Evidence — this feed goes to asset owners who act on the dates in it, so
every specific must be traceable to the source:
- Add one evidence entry for EVERY date, deadline, vote schedule, dollar
  figure, megawatt figure, percentage, or hour count that appears in either
  `summary` or `hydro_read_through`. If a specific can't be quoted from the
  document, it does not belong in either field.
- "quote" must be copied VERBATIM from the document text above — character
  for character, long enough to be findable (roughly 10-200 characters).
  Do not paraphrase, reformat, normalize a date, or repair typos: the quote
  is checked automatically against the source text, and an edited quote
  fails that check.
- If you cannot produce a verbatim quote for a specific, REMOVE THAT
  SPECIFIC FROM THE SUMMARY. Never quote from the metadata block, from the
  document title, or from your own prior sentence — only from the document
  text.
- A summary with no dates or figures is fine and needs no evidence entries;
  return [] for it. Do not manufacture specifics to have something to cite.

Topic tags — choose 1-3 that best describe what the document is ABOUT
(not every topic it brushes past). Use ONLY these exact tags:
{topic_list}

Stakeholder extraction rules:
- Include named individuals from cover pages, "submitted by" lines, "contact:" blocks,
  signature blocks, author lists, and presenter credits.
- Prefer external stakeholders (utilities, advocacy groups, trade associations,
  consultancies) over RTO/ISO staff, but include both.
- Do NOT invent or guess email addresses. If an address isn't shown, set email to null.
- Do NOT include people merely mentioned in passing (e.g. names cited in a footnote).
- Return an empty array [] if no contributors are identifiable.
"""

# 48K chars ≈ 12K tokens of Haiku input (~$0.012/doc). The old 12K budget
# truncated ~32% of documents against a ~18.6K-char average, which mattered
# more than the cost saving: the prompt demands comment deadlines and vote
# dates quoted VERBATIM, and in RTO materials those live in the closing
# pages — exactly what a head-only cut throws away. Haiku 4.5 has a 200K
# token context, so this is still a small fraction of what's available.
MAX_DOC_CHARS = 48000

# When a doc exceeds the budget, read the head AND the tail rather than the
# head alone. Front matter carries what the document IS (proposal identity,
# revision history, NPRR/tariff forms); the tail carries what a member has to
# ACT on (comment deadlines, next steps, vote schedules, contacts, signature
# blocks). Head-only reading systematically drops the actionable half.
_HEAD_FRACTION = 0.6
_ELISION_MARKER = (
    "\n\n[... MIDDLE OF DOCUMENT OMITTED FOR LENGTH — the text below "
    "resumes from a LATER page and does not continue the sentence above "
    "...]\n\n"
)


def build_excerpt(text, budget=MAX_DOC_CHARS):
    """Fit `text` into `budget` chars, keeping both ends when it doesn't fit.

    Returns (excerpt, elided). `elided` drives a prompt note so the model
    knows the two halves aren't contiguous and won't read a figure from the
    head as belonging to a table in the tail.
    """
    text = (text or "").strip()
    if len(text) <= budget:
        return text, False
    head_len = int(budget * _HEAD_FRACTION)
    tail_len = budget - head_len
    return (
        text[:head_len].rstrip() + _ELISION_MARKER + text[-tail_len:].lstrip(),
        True,
    )


# ── Directness: how squarely a document bears on hydro ──────────────────────
# Relevance stays a wide, recall-favoring gate (see SYSTEM_PROMPT) because a
# rejected doc is invisible to all three consumers forever. Directness is the
# separate axis that keeps a wide gate from reading as overclaiming: it tells
# a downstream reader whether a hit is about hydro or merely adjacent to it.
# Ordered strongest → weakest; the calendar UI's filter uses this order.
DIRECTNESS_LEVELS = {
    "direct": (
        "the document's own subject changes a hydro or PSH revenue stream, "
        "compliance obligation, or strategic option"
    ),
    "precedent": (
        "the rule targets other resources (batteries, gas, generic storage) "
        "or another market, and matters because its design would extend to "
        "or set precedent for hydro/PSH — includes ERCOT comparison content"
    ),
    "reference": (
        "background or analytical value with no specific hydro action in it "
        "— market-monitor reports, white papers, long-run studies, data"
    ),
}

_DIRECTNESS_LIST_FOR_PROMPT = "\n".join(
    f"  {k} — {v}" for k, v in DIRECTNESS_LEVELS.items())


# ── Does the source itself name hydro? ──────────────────────────────────────
# Computed in code, not asked of the model, because it's the fact the model is
# least reliable about and most consequential to get wrong. Most RTO material
# reaches hydro through a general rule ("Non-Energy Limited Resources") and
# never says the word — and that is exactly when a summary starts inventing
# hydro-specific mechanics the deck never contained. Knowing the answer lets
# the prompt forbid those sentences outright, and lets every downstream reader
# see that the hydro angle is an inference rather than something quoted.
_HYDRO_TERM_RE = re.compile(
    # hydro / hydropower / hydroelectric / hydrology, but NOT hydrogen or
    # hydrocarbon — both are common in RTO gas material and matching them
    # would relax this guardrail on exactly the documents that need it.
    r"\bhydro(?!gen|carbon)"
    r"|pumped[\s\-]?storage"
    r"|\bPSH\b"
    r"|run[\s\-]?of[\s\-]?river",
    re.IGNORECASE,
)


def source_names_hydro(text):
    """True when the document text itself uses a hydro term."""
    return bool(_HYDRO_TERM_RE.search(text or ""))


# ── Response schemas ────────────────────────────────────────────────────────
# Passed as output_config.format so the API constrains generation to this
# shape. That removes two failure modes the old free-text JSON had: markdown
# code fences needing to be stripped, and malformed JSON landing in the
# except branch (which used to mark the document NOT relevant — a silent,
# permanent false negative). Off-vocabulary topics and directness values are
# now impossible at generation time rather than filtered afterwards.
#
# Schema constraints: every object needs additionalProperties:false and must
# list all its properties in `required`; nullable fields use anyOf with an
# explicit null branch. Length/range keywords are not supported.

_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}

MEETING_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["relevant", "reason"],
    "additionalProperties": False,
}

DOCUMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "reason": {"type": "string"},
        "directness": {"anyOf": [
            {"type": "string", "enum": list(DIRECTNESS_LEVELS)},
            {"type": "null"},
        ]},
        "summary": _NULLABLE_STRING,
        "hydro_read_through": _NULLABLE_STRING,
        "topics": {
            "type": "array",
            "items": {"type": "string", "enum": list(TOPIC_TAGS)},
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["claim", "quote"],
                "additionalProperties": False,
            },
        },
        "stakeholders": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "entity": _NULLABLE_STRING,
                    "role": _NULLABLE_STRING,
                    "email": _NULLABLE_STRING,
                },
                "required": ["name", "entity", "role", "email"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["relevant", "reason", "directness", "summary",
                 "hydro_read_through", "topics", "evidence", "stakeholders"],
    "additionalProperties": False,
}


# ── API helpers ─────────────────────────────────────────────────────────────

class ScreeningError(RuntimeError):
    """A screening call that produced no usable result.

    Raised rather than returned so the caller's handler skips the save
    entirely: the row keeps ai_processed_at IS NULL and is retried on the
    next run. The old code returned relevant=False on a parse failure, which
    wrote a permanent "not relevant" verdict for what was really a transport
    problem (45 documents in the DB carry a 'parse error' reason from that).
    """


def _call_claude(client, prompt, schema, max_tokens=256):
    """Call Claude with a constrained output shape. Returns the parsed dict.

    Raises ScreeningError on truncation, refusal, or an empty response —
    never a partial or invented result.
    """
    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )

    # Truncation would leave the constrained JSON half-written. Treat it as
    # retryable rather than parsing whatever arrived.
    if message.stop_reason == "max_tokens":
        raise ScreeningError(
            f"response hit max_tokens ({max_tokens}) — raise the budget")
    if message.stop_reason == "refusal":
        raise ScreeningError("model declined to answer (stop_reason=refusal)")

    text = next((b.text for b in message.content if b.type == "text"), None)
    if not text:
        raise ScreeningError(
            f"no text block in response (stop_reason={message.stop_reason})")

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # output_config makes this near-impossible; keep it explicit so a
        # surprise surfaces as a retry instead of a false "not relevant".
        raise ScreeningError(f"unparseable JSON despite schema: {e}") from e


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

    result = _call_claude(client, prompt, MEETING_SCHEMA, max_tokens=128)
    return bool(result.get("relevant", False)), result.get("reason", "")


# Typography folded away before matching a quote against its source. RTO
# documents are Word/PDF exports full of en dashes, curly quotes, and NBSPs,
# and a model transcribing a span reliably normalizes those — quoting
# "Appendix V - Solar" for a source that holds "Appendix V – Solar" (a real
# case from MISO BPM-011). Folding them keeps the check honest about CONTENT
# while not raising a fabrication alarm over a dash. Anything beyond
# whitespace and punctuation shape must still match exactly.
_MATCH_FOLD = str.maketrans({
    # dashes and minus signs -> hyphen
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-", "­": "-",
    # single quotes / apostrophes -> '
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "′": "'", "´": "'", "`": "'",
    # double quotes -> "
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "″": '"',
    # misc
    "…": "...", " ": " ", "​": "",
})


def _normalize_for_match(s):
    """Fold whitespace and typography so a real quote isn't failed on shape.

    Extracted text carries layout-driven newlines and runs of spaces, plus
    publisher typography; a model quoting a span normalizes both. Collapsing
    them prevents false "unverified" flags, which matter because a flag
    nobody trusts is a flag nobody reads.
    """
    return " ".join((s or "").translate(_MATCH_FOLD).split())


def verify_evidence(evidence, excerpt):
    """Check each quote actually appears in the text the model was given.

    Returns (entries, unverified_count) where each entry gains a `verified`
    flag. This is the deterministic half of the grounding check: the prompt
    asks for verbatim spans, and this confirms they are verbatim instead of
    trusting it. A quote that fails is kept — flagged, not deleted — so the
    fabrication stays visible downstream rather than being quietly dropped.
    """
    haystack = _normalize_for_match(excerpt)
    entries, unverified = [], 0
    for e in evidence:
        if not isinstance(e, dict):
            continue
        quote = (e.get("quote") or "").strip()
        ok = bool(quote) and _normalize_for_match(quote) in haystack
        if not ok:
            unverified += 1
        entries.append({
            "claim": (e.get("claim") or "").strip(),
            "quote": quote,
            "verified": ok,
        })
    return entries, unverified


def screen_document(client, doc_row, dry_run=False):
    """
    Stage 2: Screen a document by title + text excerpt.
    Returns (relevant, reason, summary, topics, stakeholders, directness,
             evidence, hydro_read_through, names_hydro).
    """
    excerpt, elided = build_excerpt(doc_row["extracted_text"])
    # Judged on the excerpt, not the full text: it must describe the text the
    # model actually saw, or the instruction below would be wrong for the
    # portion that was omitted.
    names_hydro = source_names_hydro(excerpt)

    prompt = DOCUMENT_PROMPT.format(
        rto=doc_row["rto"] or "",
        committee=doc_row["committee"] or "",
        meeting_date=doc_row["meeting_date"] or "",
        meeting_title=doc_row["meeting_title"] or "",
        doc_title=doc_row["title"] or doc_row["filename"] or "",
        doc_type=doc_row["doc_type"] or "",
        text_excerpt=excerpt or "(no text extracted — screening title only)",
        topic_list=_TOPIC_LIST_FOR_PROMPT,
        directness_list=_DIRECTNESS_LIST_FOR_PROMPT,
        elision_note=(
            "- This document was too long to include whole. You are seeing "
            "its opening AND its closing, joined by an omission marker. Text "
            "on either side of that marker is NOT contiguous — do not carry "
            "a heading, table, or figure across it.\n"
            if elided else ""
        ),
        # Checked in code before the call, so this is a fact rather than the
        # model's impression of one. Stated bluntly because this is the exact
        # situation that produces invented hydro mechanics.
        hydro_mention_note=(
            "\nCHECKED AUTOMATICALLY: the document text above does NOT contain "
            "the words hydro, hydropower, pumped storage, PSH, or run-of-river "
            "anywhere. Therefore `summary` must not mention any of them, and "
            "`hydro_read_through` must open by noting the document doesn't "
            "address hydro directly. Any per-technology rule you are tempted "
            "to attribute to it would be invented.\n"
            if not names_hydro else
            "\nCHECKED AUTOMATICALLY: the document text above does use hydro "
            "terminology, so `summary` may report what it actually says about "
            "hydro — but only what it says, not what you infer from it.\n"
        ),
    )

    if dry_run:
        print(f"\n--- DRY RUN (doc {doc_row['id']}) ---")
        print(prompt[:600], "...")
        return True, "dry-run", None, [], [], None, [], None, names_hydro

    # 2400 rather than the old 900: the evidence array adds a verbatim quote
    # per date/figure and hydro_read_through is a second prose field, and a
    # truncated response is now a hard failure (and a retry) instead of a
    # silently mis-saved one.
    result = _call_claude(client, prompt, DOCUMENT_SCHEMA, max_tokens=2400)

    stakeholders = result.get("stakeholders") or []
    if not isinstance(stakeholders, list):
        stakeholders = []
    # The schema's enum already constrains these; the filter stays as a
    # backstop and to enforce the 3-tag cap, which a schema can't express.
    topics = result.get("topics") or []
    if not isinstance(topics, list):
        topics = []
    topics = [t for t in topics if t in TOPIC_TAGS][:3]

    relevant = bool(result.get("relevant", False))
    directness = result.get("directness")
    if directness not in DIRECTNESS_LEVELS:
        directness = None
    # A relevant doc with no directness would be indistinguishable from the
    # pre-directness backlog, which the UI treats as unrated and always
    # shows. Default it to the weakest tier so it can't leak through as
    # unrated and thereby dodge the filter entirely.
    if relevant and directness is None:
        directness = "reference"
    if not relevant:
        directness = None

    evidence, _ = verify_evidence(result.get("evidence") or [], excerpt)

    return (
        relevant,
        result.get("reason", ""),
        result.get("summary"),
        topics,
        stakeholders,
        directness,
        evidence,
        result.get("hydro_read_through") if relevant else None,
        names_hydro,
    )


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
            # Same hazard as Stage 2: the dry-run verdict is a placeholder
            # (relevant=True, reason="dry-run"), and saving it would mark every
            # matched meeting hydro-relevant — which then opens the Stage-2 gate
            # for all of their documents.
            if not dry_run:
                save_meeting_screening(conn, m["id"], relevant, reason)
            flag = "YES" if relevant else "no"
            print(f"{flag} — {reason[:80]}")
            if relevant:
                relevant_count += 1
        except Exception as e:
            print(f"ERROR: {e}")

    print(f"\n  Stage 1 complete: {relevant_count}/{len(meetings)} meetings flagged as relevant")
    return relevant_count


def run_stage2(conn, client, rto_filter=None, rescreen=False, limit=200,
               dry_run=False, since=None, until=None, only_unrated=False):
    """Screen documents for meetings that passed Stage 1.

    `since`/`until` bound the MEETING date (inclusive, YYYY-MM-DD). Pair them
    with --rescreen to refresh a specific window — e.g. the few weeks the
    weekly digest actually reads — without paying to reprocess the archive.

    `only_unrated` further narrows to documents that predate the current
    screening fields. That's what makes a rescreen resumable: an interrupted run
    can be restarted without paying to redo — and without re-rolling — the
    documents it already finished, including the ones it judged not relevant.
    """
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
    if since:
        where.append("m.meeting_date >= ?")
        params.append(since)
    if until:
        where.append("m.meeting_date <= ?")
        params.append(until)
    if only_unrated:
        # `source_names_hydro`, not `directness`, is the "already screened under
        # the current prompt" signal: it is written on every save, while directness
        # is NULL for any document judged not relevant. Testing directness would
        # treat every not-relevant document as unfinished and re-screen it on
        # every resume — on this window that was 155 documents of pure rework.
        where.append("d.source_names_hydro IS NULL")

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
    truncated_count = 0
    unverified_count = 0
    leaked_count = 0
    directness_counts = {k: 0 for k in DIRECTNESS_LEVELS}

    for i, doc in enumerate(docs, 1):
        label = (doc["title"] or doc["filename"] or "untitled")[:60]
        doc_len = len(doc["extracted_text"] or "")
        if doc_len:
            text_note = f"{doc_len} chars"
            if doc_len > MAX_DOC_CHARS:
                text_note += " (head+tail)"
                truncated_count += 1
        else:
            text_note = "no text"
        print(f"  [{i}/{len(docs)}] {doc['rto']} | {label} ({text_note})", end=" ... ", flush=True)

        try:
            (relevant, reason, summary, topics, stakeholders, directness,
             evidence, read_through, names_hydro) = screen_document(
                client, doc, dry_run)
            # A dry run must not persist anything. screen_document returns a
            # placeholder verdict (relevant=True, reason="dry-run") so the loop
            # can go through its motions, and saving that overwrites real
            # screening results with a fabricated one — it silently marked 113
            # documents hydro-relevant with reason "dry-run" before this guard
            # existed. Recovering needed the previous commit's copy of the DB.
            if not dry_run:
                save_ai_screening(conn, doc["id"], relevant, reason, summary,
                                  topics=topics, directness=directness,
                                  evidence=evidence,
                                  hydro_read_through=read_through,
                                  source_names_hydro=names_hydro)
                save_document_stakeholders(
                    conn, doc["id"], stakeholders,
                    source_text=doc["extracted_text"]
                )
            stakeholder_count += len(stakeholders)
            if directness in directness_counts:
                directness_counts[directness] += 1

            unverified = [e for e in evidence if not e["verified"]]
            unverified_count += len(unverified)

            # The failure this whole split exists to catch: a summary that
            # names hydro when the source never did. Reported per-document
            # because it means the summary is asserting something the document
            # doesn't contain, which no amount of downstream care can undo.
            leaked = bool(summary) and not names_hydro and source_names_hydro(summary)
            if leaked:
                leaked_count += 1

            flag = "YES" if relevant else "no"
            extras = f", {len(stakeholders)} stakeholders" if stakeholders else ""
            if directness:
                extras = f" [{directness}]" + extras
            if evidence:
                extras += f", {len(evidence) - len(unverified)}/{len(evidence)} cited"
            if leaked:
                extras += ", SUMMARY NAMES HYDRO (source doesn't)"
            print(f"{flag}{extras} — {reason[:80]}")
            # Surface unverifiable quotes per-doc: a quote that isn't in the
            # source is the one signal that a specific may be invented.
            for e in unverified:
                print(f"        ! unverified: {e['claim'][:50]} <- "
                      f"\"{e['quote'][:60]}\"")
            if relevant:
                relevant_count += 1
        except Exception as e:
            # Nothing is saved on failure, so ai_processed_at stays NULL and
            # this document is picked up again on the next run.
            print(f"ERROR: {e}")
            error_count += 1

    print(f"\n  Stage 2 complete: {relevant_count}/{len(docs)} documents flagged as relevant; "
          f"{stakeholder_count} stakeholders extracted")
    if any(directness_counts.values()):
        breakdown = ", ".join(f"{k}={v}" for k, v in directness_counts.items())
        print(f"  Directness: {breakdown}")
    if truncated_count:
        print(f"  {truncated_count} document(s) exceeded {MAX_DOC_CHARS} chars "
              f"— read as head+tail with the middle omitted")
    if unverified_count:
        print(f"  WARNING: {unverified_count} cited quote(s) could not be "
              f"found in the source text (flagged in the export, not dropped)")
    if leaked_count:
        print(f"  WARNING: {leaked_count} summary/summaries name hydro where "
              f"the source never does — the hydro argument belongs in "
              f"hydro_read_through, not the summary")
    return relevant_count, error_count


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    # The progress log echoes model output and now verbatim source quotes,
    # and RTO materials are full of characters the Windows console codepage
    # can't encode (≤, ±, °, ≥, smart quotes). Without this, a single such
    # character raises UnicodeEncodeError mid-loop — which the per-document
    # handler would count as a screening failure even though the row saved
    # fine. Replace unencodable characters instead of failing the write.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

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
        "--since", metavar="YYYY-MM-DD",
        help="Stage 2: only documents whose MEETING date is on/after this"
    )
    parser.add_argument(
        "--until", metavar="YYYY-MM-DD",
        help="Stage 2: only documents whose MEETING date is on/before this. "
             "With --rescreen, use these to refresh just the weeks the digest "
             "reads instead of the whole archive."
    )
    parser.add_argument(
        "--only-unrated", action="store_true",
        help="Stage 2: skip documents that already carry a directness rating. "
             "Use with --rescreen to resume an interrupted rescreen without "
             "paying to redo the documents it already finished."
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
                   dry_run=args.dry_run,
                   since=args.since,
                   until=args.until,
                   only_unrated=args.only_unrated)

    conn.close()
    print("\nDone. Run: python run_scrapers.py --export-only  to refresh the calendar JSON.")


if __name__ == "__main__":
    main()
