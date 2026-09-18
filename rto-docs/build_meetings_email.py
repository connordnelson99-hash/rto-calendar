#!/usr/bin/env python3
"""Build the "Meetings Ahead" HTML block for the weekly RTO/ISO Hydro Digest email.

Reads the published events feed (rto_events_with_docs.json) and emits an
Outlook-safe HTML fragment: a table of upcoming meetings (chip colors matched
to the live calendar UI) followed by a bulletproof "Open the full Markets
Calendar" button. The fragment is meant to be pasted/injected at the end of the
digest email by the rto-hydro-digest-email skill.

Outlook desktop renders email with Word's HTML engine, so the markup here is
deliberately old-school: nested tables, all-inline styles, no flexbox/grid, no
external CSS, no JavaScript.

SELECTION LIVES IN THE SKILL, NOT HERE.
---------------------------------------
Which meetings make the block is an editorial judgment, and it has to be the
same judgment the rest of the email is written under: the inclusion bar, the
ranking signals, and the member-footprint weighting in the
rto-hydro-digest-email skill. Encoding a second copy of that rubric in Python
would guarantee the two drift apart, so this script does not try. The intended
flow is two passes:

    1. --json  emits the window's hydro-relevant meetings as candidates, with
       the context needed to judge them (committee, initiative phase, posted
       document titles, the screener's reason).
    2. --pick  renders an explicitly chosen set of candidate ids.

Between those two calls the agent applies the protocol. See the "Meetings
Ahead block" section of the skill for how.

Run with neither flag and the script falls back to a deterministic selection:
footprint-ordered, capped per RTO, which approximates the protocol without
applying it. That path exists so an unattended run still produces something
defensible; it is not the intended path. --round-robin restores the original
breadth-first behavior, which deliberately contradicts the footprint weighting
and is kept only for comparison.

Usage:
    python build_meetings_email.py --today 2026-08-03 --json
    python build_meetings_email.py --today 2026-08-03 --pick caiso-0805-1f3a,pjm-0805-77b2 --out block.html
    python build_meetings_email.py --days 14        # rolling 14-day window instead of the work week
    python build_meetings_email.py --all            # include non-hydro meetings too
    python build_meetings_email.py --calendar-url https://nha-wordpress-page/...
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys

# RTO chip palette — mirrors webcal-v2/data.js (rtoMeta). Keep extra keys
# (NEPOOL/NERC/Other) for forward-compat even if absent from the current feed.
RTO_META = {
    "PJM":            {"color": "#3B82F6", "bg": "#EFF6FF", "label": "PJM"},
    "CAISO":          {"color": "#F59E0B", "bg": "#FFFBEB", "label": "CAISO"},
    "MISO":           {"color": "#10B981", "bg": "#ECFDF5", "label": "MISO"},
    "NYISO":          {"color": "#EF4444", "bg": "#FEF2F2", "label": "NYISO"},
    "ERCOT":          {"color": "#A855F7", "bg": "#FAF5FF", "label": "ERCOT"},
    "ISO-NE":         {"color": "#06B6D4", "bg": "#ECFEFF", "label": "ISO-NE"},
    "SPP Markets +":  {"color": "#D97706", "bg": "#FEF3C7", "label": "SPP Markets+"},
    "SPP":            {"color": "#92400E", "bg": "#FEF7ED", "label": "SPP West"},
    "NEPOOL":         {"color": "#0891B2", "bg": "#ECFEFF", "label": "NEPOOL"},
    "NERC":           {"color": "#EC4899", "bg": "#FDF2F8", "label": "NERC"},
    "FERC":           {"color": "#64748B", "bg": "#F1F5F9", "label": "FERC"},
    "Other":          {"color": "#94A3B8", "bg": "#F8FAFC", "label": "Other"},
}
_FALLBACK_META = {"color": "#64748B", "bg": "#F1F5F9", "label": "RTO"}

DEFAULT_CALENDAR_URL = "https://connordnelson99-hash.github.io/rto-calendar/webcal-v2/"

# Member-footprint tiers — mirrors "Which RTO sections to include, and in what
# order" in the rto-hydro-digest-email skill, which orders by member-owned
# assets rather than headquarters location. Used for the deterministic
# fallback's ordering and reported on every candidate so the agent doesn't have
# to hold the order in its head. Lower number = closer to member assets.
#
#   1  CAISO / West, including WEIM, EDAM, Markets+ and Western market formation
#   2  PJM and NYISO, near-peers
#   3  ISO-NE
#   4  FERC, which reaches the non-RTO Southeast
#   5  MISO / SPP West / ERCOT, which must clear a higher bar
#
# "SPP Markets +" sits in tier 1 as Western market formation while plain "SPP"
# (SPP West) sits in tier 5 with the higher-bar group, following the skill's
# own split between the two.
FOOTPRINT_TIER = {
    "CAISO": 1,
    "SPP Markets +": 1,
    "PJM": 2,
    "NYISO": 2,
    "ISO-NE": 3,
    "FERC": 4,
    "MISO": 5,
    "SPP": 5,
    "ERCOT": 5,
}
_DEFAULT_TIER = 6

# Max rows one RTO may occupy, mirroring the email's "maximum 2 ranked items
# per RTO". Overridable with --per-rto; disable with --no-caps.
DEFAULT_PER_RTO_CAP = 2

# Brand-ish neutrals used for the table chrome.
INK = "#0F172A"
MUTED = "#64748B"
HAIR = "#E2E8F0"
HYDRO = "#0E7490"  # button / accent — matches the calendar's hydro accent family


def parse_date(s):
    """Feed dates are ISO (YYYY-MM-DD). Return a date or None."""
    try:
        return dt.date.fromisoformat(s.strip())
    except (ValueError, AttributeError):
        return None


def best_link(ev):
    """Prefer the meeting detail page, then materials, then the RTO calendar."""
    for key in ("detail_url", "materials_url", "source_url"):
        url = (ev.get(key) or "").strip()
        if url:
            return url
    return ""


def clean_title(ev):
    """Drop a leading RTO token so the chip isn't echoed in the title."""
    title = (ev.get("title") or "").strip()
    label = RTO_META.get(ev.get("rto", ""), _FALLBACK_META)["label"]
    for token in (label, ev.get("rto", ""), label.replace("+", "").strip()):
        token = (token or "").strip()
        if token and title.lower().startswith(token.lower() + " "):
            title = title[len(token):].strip()
            break
    return title


def footprint_tier(rto):
    return FOOTPRINT_TIER.get(rto, _DEFAULT_TIER)


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def event_id(ev):
    """Stable, short, human-typable id for a feed event.

    The feed carries no primary key, so derive one from the fields that
    identify a meeting: RTO, date, committee, title. Same meeting in a later
    feed rebuild keeps the same id; a retitled meeting gets a new one, which is
    the safe direction to fail — a --pick against a stale id errors loudly
    instead of silently rendering the wrong row.
    """
    rto = _SLUG_STRIP.sub("-", (ev.get("rto") or "rto").lower()).strip("-") or "rto"
    date = (ev.get("date") or "")
    mmdd = date[5:7] + date[8:10] if len(date) >= 10 else "0000"
    basis = "|".join([
        ev.get("rto") or "",
        date,
        ev.get("committee") or "",
        ev.get("title") or "",
    ])
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:4]
    return f"{rto}-{mmdd}-{digest}"


def candidate_payload(selected, start, end, label):
    """Everything the agent needs to apply the inclusion bar to a meeting.

    Deliberately more than the renderer uses. Prospective selection is
    materials-poor by nature, so the fields that survive that scarcity carry
    the weight: the committee, the initiative's stakeholder phase, whatever
    documents have been posted in advance, and the Stage-1 screening reason.
    """
    out = []
    for d, ev in selected:
        docs = ev.get("documents") or []
        hydro_docs = [doc for doc in docs if doc.get("hydro_relevant")]
        topics = []
        for doc in hydro_docs:
            for t in (doc.get("topics") or []):
                if t not in topics:
                    topics.append(t)
        directness = sorted({
            doc.get("directness") for doc in hydro_docs if doc.get("directness")
        })
        out.append({
            "id": event_id(ev),
            "date": ev.get("date"),
            "weekday": d.strftime("%a"),
            "time": (ev.get("time") or "").strip(),
            "rto": ev.get("rto"),
            "footprint_tier": footprint_tier(ev.get("rto")),
            "committee": (ev.get("committee") or "").strip(),
            "title": (ev.get("title") or "").strip(),
            "detail_url": ev.get("detail_url") or "",
            "materials_url": ev.get("materials_url") or "",
            "source_url": ev.get("source_url") or "",
            # Stage-1 verdict: judged from title and committee alone, with no
            # materials. Useful as a starting point, not as a ranking.
            "screened_reason": ev.get("meeting_hydro_reason") or "",
            "initiatives": [
                {
                    "name": iss.get("canonical_name"),
                    "stakeholder_phase": iss.get("stakeholder_phase"),
                    "status": iss.get("status"),
                    "is_open": bool(iss.get("is_open")),
                    "url": iss.get("url"),
                }
                for iss in (ev.get("issues") or [])
            ],
            "doc_count": len(docs),
            "hydro_doc_count": len(hydro_docs),
            "hydro_doc_titles": [doc.get("title") for doc in hydro_docs[:8]],
            "hydro_doc_topics": topics,
            "hydro_doc_directness": directness,
        })
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "label": label},
        "count": len(out),
        "candidates": out,
    }


def cap_per_rto(rows, cap):
    """Drop rows past `cap` for any one RTO, keeping the earliest. Mirrors the
    email's 2-ranked-items-per-RTO budget."""
    if not cap:
        return rows, []
    kept, dropped, seen = [], [], {}
    for d, ev in rows:
        rto = ev.get("rto", "")
        seen[rto] = seen.get(rto, 0) + 1
        (kept if seen[rto] <= cap else dropped).append((d, ev))
    return kept, dropped


def footprint_select(selected, limit, cap):
    """Deterministic fallback: footprint tier first, then date.

    This is an approximation of the protocol, not the protocol. It knows where
    member assets are but nothing about whether a given meeting clears the
    inclusion bar, so it will happily seat a routine tier-1 committee over a
    tier-5 meeting with a comment deadline attached. Prefer --pick.
    """
    kept, _ = cap_per_rto(selected, cap)
    kept.sort(key=lambda t: (footprint_tier(t[1].get("rto")), t[0],
                             (t[1].get("time") or "")))
    chosen = kept[:limit] if limit else kept
    chosen.sort(key=lambda t: (t[0], (t[1].get("time") or "")))
    return chosen


def pick_select(selected, ids, cap):
    """Render exactly the ids the agent chose, in date order.

    Raises on anything that would quietly produce the wrong block: an id that
    isn't in the window, a duplicate, or a per-RTO overflow.
    """
    by_id = {event_id(ev): (d, ev) for d, ev in selected}

    seen, unknown, dupes = set(), [], []
    for i in ids:
        if i in seen:
            dupes.append(i)
        elif i not in by_id:
            unknown.append(i)
        seen.add(i)

    if unknown:
        raise SystemExit(
            f"--pick: {len(unknown)} id(s) not among the window's hydro-relevant "
            f"meetings: {', '.join(unknown)}\n"
            f"Re-run with --json to get current ids (they change if a meeting is "
            f"retitled or rescreened)."
        )
    if dupes:
        raise SystemExit(f"--pick: duplicate id(s): {', '.join(sorted(set(dupes)))}")

    chosen = [by_id[i] for i in dict.fromkeys(ids)]
    chosen.sort(key=lambda t: (t[0], (t[1].get("time") or "")))

    if cap:
        counts = {}
        for _, ev in chosen:
            counts[ev.get("rto", "")] = counts.get(ev.get("rto", ""), 0) + 1
        over = {r: n for r, n in counts.items() if n > cap}
        if over:
            detail = ", ".join(f"{r}: {n}" for r, n in sorted(over.items()))
            raise SystemExit(
                f"--pick: per-RTO cap of {cap} exceeded ({detail}). The email "
                f"allows a maximum of {cap} ranked items per RTO and the block "
                f"follows the same budget. Drop the weaker rows, or pass "
                f"--per-rto N / --no-caps if this week genuinely warrants it."
            )
    return chosen


def work_week_bounds(today):
    """The next full work week (Mon-Fri). If today is a Monday, that's the
    current week; any other day rolls forward to next Monday. So a digest
    compiled Mon shows this week, and one compiled Tue-Sun shows the week
    ahead."""
    wd = today.weekday()  # Mon=0 .. Sun=6
    monday = today if wd == 0 else today + dt.timedelta(days=7 - wd)
    return monday, monday + dt.timedelta(days=4)


def _fmt_day(d):
    return d.strftime("%b %-d") if os.name != "nt" else d.strftime("%b %#d")


def window_label(start, end, days):
    if days is not None:
        return f"the next {days} days"
    if start.month == end.month:
        end_part = str(end.day)
    else:
        end_part = _fmt_day(end)
    return f"the week of {_fmt_day(start)}&ndash;{end_part}"


def select_events(events, start, end, include_all):
    out = []
    for ev in events:
        d = parse_date(ev.get("date", ""))
        if d is None or d < start or d > end:
            continue
        if not include_all and not ev.get("meeting_hydro_relevant"):
            continue
        out.append((d, ev))
    out.sort(key=lambda t: (t[0], (t[1].get("time") or "")))
    return out


def diversify(selected, limit):
    """Pick up to `limit` rows round-robin across RTOs so one busy RTO can't
    dominate the quick view. RTOs are visited in order of their earliest
    meeting; within an RTO, earliest meetings go first. The result is
    re-sorted chronologically for display."""
    if not limit or len(selected) <= limit:
        return selected[:limit] if limit else selected

    groups = {}  # rto -> date-sorted list; dict preserves first-seen order
    for d, ev in selected:  # selected is already date-sorted
        groups.setdefault(ev.get("rto", ""), []).append((d, ev))

    queues = list(groups.values())
    idx = [0] * len(queues)
    chosen = []
    while len(chosen) < limit:
        progressed = False
        for i, q in enumerate(queues):
            if idx[i] < len(q):
                chosen.append(q[idx[i]])
                idx[i] += 1
                progressed = True
                if len(chosen) >= limit:
                    break
        if not progressed:
            break

    chosen.sort(key=lambda t: (t[0], (t[1].get("time") or "")))
    return chosen


def chip_html(rto):
    m = RTO_META.get(rto, dict(_FALLBACK_META, label=rto or "RTO"))
    return (
        '<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
        'font-size:11px;font-weight:600;line-height:1.4;white-space:nowrap;'
        'font-family:Arial,Helvetica,sans-serif;'
        f'background:{m["bg"]};color:{m["color"]};border:1px solid {m["color"]}33;">'
        f'{html.escape(m["label"])}</span>'
    )


def row_html(d, ev, show_date=True):
    link = best_link(ev)
    title = html.escape(clean_title(ev)) or "(untitled meeting)"
    if link:
        title = (
            f'<a href="{html.escape(link, quote=True)}" '
            f'style="color:{INK};text-decoration:none;font-weight:600;">{title}</a>'
        )
    else:
        title = f'<span style="color:{INK};font-weight:600;">{title}</span>'

    # Show the date only on the first row of each day so the table reads like
    # a grouped agenda instead of repeating "Tue Jun 16" six times.
    if show_date:
        date_str = d.strftime("%a %b %-d") if os.name != "nt" else d.strftime("%a %b %#d")
    else:
        date_str = ""
    time_str = html.escape((ev.get("time") or "").strip())
    committee = html.escape((ev.get("committee") or "").strip())

    meta_bits = []
    if time_str:
        meta_bits.append(time_str)
    if committee:
        meta_bits.append(committee)
    meta_line = (
        f'<div style="color:{MUTED};font-size:12px;line-height:1.5;'
        f'font-family:Arial,Helvetica,sans-serif;margin-top:2px;">'
        f'{" &middot; ".join(meta_bits)}</div>'
        if meta_bits else ""
    )

    return (
        f'<tr>'
        f'<td valign="top" width="92" style="padding:10px 12px 10px 0;border-bottom:1px solid {HAIR};'
        f'white-space:nowrap;color:{INK};font-size:13px;font-weight:600;'
        f'font-family:Arial,Helvetica,sans-serif;">{html.escape(date_str)}</td>'
        f'<td valign="top" style="padding:10px 12px 10px 0;border-bottom:1px solid {HAIR};">'
        f'{chip_html(ev.get("rto", ""))}</td>'
        f'<td valign="top" style="padding:10px 0;border-bottom:1px solid {HAIR};'
        f'font-size:14px;line-height:1.45;font-family:Arial,Helvetica,sans-serif;">'
        f'{title}{meta_line}</td>'
        f'</tr>'
    )


def button_html(url):
    """Bulletproof-ish CTA button. Word/Outlook ignores border-radius but the
    bgcolor + padded anchor still renders as a solid filled button."""
    url = html.escape(url, quote=True)
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:18px 0 4px 0;"><tr>'
        f'<td bgcolor="{HYDRO}" style="border-radius:6px;">'
        f'<a href="{url}" '
        'style="display:inline-block;padding:11px 22px;font-family:Arial,Helvetica,sans-serif;'
        'font-size:14px;font-weight:700;color:#FFFFFF;text-decoration:none;border-radius:6px;">'
        'Open the full Markets Calendar &rarr;</a>'
        '</td></tr></table>'
    )


def build_html(shown, total, calendar_url, label, today):
    """Render the block. `shown` is already-chosen and date-sorted; `total` is
    the size of the hydro-relevant window, which drives the "+N more" line.

    Rows display chronologically regardless of how they were chosen. Footprint
    weighting decides which meetings survive, not what order a member reads
    them in; a week-ahead agenda that isn't in date order is useless.
    """
    if shown:
        # Collapse repeated date labels: only the first row of each day shows it.
        parts, prev = [], None
        for d, ev in shown:
            parts.append(row_html(d, ev, show_date=(d != prev)))
            prev = d
        rows = "\n".join(parts)

        more = total - len(shown)
        more_row = (
            f'<tr><td colspan="3" style="padding:12px 0 0 0;font-size:13px;'
            f'font-family:Arial,Helvetica,sans-serif;color:{MUTED};">'
            f'+{more} more meeting{"s" if more != 1 else ""} {label} &mdash; '
            f'<a href="{html.escape(calendar_url, quote=True)}" style="color:{HYDRO};'
            f'font-weight:600;text-decoration:none;">see the full calendar</a></td></tr>'
            if more > 0 else ""
        )
        body = (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            'width="100%" style="border-collapse:collapse;width:100%;">'
            f'{rows}{more_row}</table>'
        )
        count_note = f"{total} hydro-relevant meeting{'s' if total != 1 else ''} &middot; {label}"
    else:
        body = (
            f'<p style="color:{MUTED};font-size:14px;font-family:Arial,Helvetica,sans-serif;">'
            f'No hydro-relevant meetings {label}. '
            'Open the calendar for the full schedule.</p>'
        )
        count_note = f"No hydro-relevant meetings &middot; {label}"

    return (
        f'<!-- Meetings Ahead block (generated {today.isoformat()}) -->\n'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="100%" style="max-width:640px;border-collapse:collapse;">'
        '<tr><td style="padding:24px 0 0 0;">'
        f'<div style="border-top:2px solid {INK};padding-top:16px;">'
        f'<h2 style="margin:0 0 2px 0;font-size:18px;color:{INK};'
        'font-family:Arial,Helvetica,sans-serif;">Meetings Ahead</h2>'
        f'<div style="color:{MUTED};font-size:12px;margin-bottom:14px;'
        f'font-family:Arial,Helvetica,sans-serif;">{count_note} &middot; '
        'join the conversation</div>'
        f'{body}'
        f'{button_html(calendar_url)}'
        '</div>'
        '</td></tr></table>'
    )


def read_pick_ids(pick, pick_file):
    """Ids from --pick or --pick-file. Accepts commas, newlines, or both, and
    ignores blank lines and # comments so a pick list can carry the one-line
    rationale that justified each row."""
    if pick_file:
        with open(pick_file, encoding="utf-8") as fh:
            raw = fh.read()
        raw = "\n".join(line.split("#", 1)[0] for line in raw.splitlines())
    elif pick:
        raw = pick
    else:
        return []
    return [tok.strip() for tok in re.split(r"[,\s]+", raw) if tok.strip()]


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default_feed = os.path.join(here, "rto_events_with_docs.json")

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feed", default=default_feed, help="path to events JSON feed")
    ap.add_argument("--days", type=int, default=None,
                    help="use a rolling N-day window instead of the default next work week (Mon-Fri)")
    ap.add_argument("--today", default=None, help="reference date YYYY-MM-DD (default: today)")
    ap.add_argument("--all", action="store_true", dest="include_all",
                    help="include non-hydro-relevant meetings (default: hydro-relevant only)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap rows shown; remainder collapses into a '+N more' link")
    ap.add_argument("--calendar-url", default=DEFAULT_CALENDAR_URL,
                    help="URL for the 'Open the full Markets Calendar' button")
    ap.add_argument("--out", default=None, help="write HTML here instead of stdout")

    sel = ap.add_argument_group(
        "selection",
        "Which meetings make the block is an editorial call. Emit candidates "
        "with --json, apply the skill's inclusion bar and ranking signals, "
        "then render the result with --pick.")
    sel.add_argument("--json", action="store_true", dest="emit_json",
                     help="emit the window's candidates as JSON instead of HTML")
    sel.add_argument("--pick", default=None,
                     help="comma-separated candidate ids to render, in place of "
                          "the deterministic fallback")
    sel.add_argument("--pick-file", default=None,
                     help="read ids from a file (one per line, or comma-separated; "
                          "blank lines and # comments ignored)")
    sel.add_argument("--per-rto", type=int, default=DEFAULT_PER_RTO_CAP,
                     help=f"max rows per RTO, mirroring the email's ranked-item "
                          f"budget (default: {DEFAULT_PER_RTO_CAP})")
    sel.add_argument("--no-caps", action="store_true",
                     help="disable the per-RTO cap")
    sel.add_argument("--round-robin", action="store_true",
                     help="legacy breadth-first selection, one row per RTO in "
                          "rotation. Contradicts the footprint weighting; kept "
                          "for comparison only")
    args = ap.parse_args(argv)

    if args.pick and args.pick_file:
        ap.error("pass --pick or --pick-file, not both")
    if args.emit_json and (args.pick or args.pick_file):
        ap.error("--json emits candidates; it does not render a picked set")

    cap = None if args.no_caps else args.per_rto

    today = parse_date(args.today) if args.today else dt.date.today()
    if today is None:
        ap.error(f"could not parse --today {args.today!r} (expected YYYY-MM-DD)")

    with open(args.feed, encoding="utf-8") as fh:
        events = json.load(fh)

    if args.days is not None:
        start, end = today, today + dt.timedelta(days=args.days)
    else:
        start, end = work_week_bounds(today)
    label = window_label(start, end, args.days)

    selected = select_events(events, start, end, args.include_all)

    if args.emit_json:
        payload = candidate_payload(selected, start, end, label)
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
            print(f"Wrote {payload['count']} candidates for {label} -> {args.out}",
                  file=sys.stderr)
        else:
            sys.stdout.write(text + "\n")
        return

    ids = read_pick_ids(args.pick, args.pick_file)
    if ids:
        shown = pick_select(selected, ids, cap)
        how = f"picked {len(shown)}"
    elif args.round_robin:
        shown = diversify(selected, args.limit)
        how = f"round-robin {len(shown)}"
    else:
        shown = footprint_select(selected, args.limit, cap)
        how = f"fallback (footprint) {len(shown)}"
        print("note: no --pick given, using the deterministic footprint fallback. "
              "It orders by member footprint but cannot apply the inclusion bar; "
              "run --json and pick if this block is going to members.",
              file=sys.stderr)

    out_html = build_html(shown, len(selected), args.calendar_url, label, today)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out_html)
        print(f"Wrote block for {label}: {len(selected)} hydro-relevant meetings, "
              f"{how} -> {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(out_html + "\n")


if __name__ == "__main__":
    main()
