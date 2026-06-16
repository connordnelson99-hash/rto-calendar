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

Selection favors breadth: meetings are hydro-relevant only, and when a row cap
is set the rows are picked round-robin across RTOs so one busy RTO (PJM, say)
can't crowd out the rest of the quick view.

Usage:
    python build_meetings_email.py                  # next full work week (Mon-Fri), hydro-relevant -> stdout
    python build_meetings_email.py --days 14        # rolling 14-day window instead of the work week
    python build_meetings_email.py --all            # include non-hydro meetings too
    python build_meetings_email.py --today 2026-06-16 --out block.html
    python build_meetings_email.py --calendar-url https://nha-wordpress-page/...
"""
import argparse
import datetime as dt
import html
import json
import os
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

DEFAULT_CALENDAR_URL = "https://connordnelson99-hash.github.io/rto-calendar/webcal.html"

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


def build_html(selected, calendar_url, label, today, limit=None):
    total = len(selected)
    shown = diversify(selected, limit)
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
    args = ap.parse_args(argv)

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
    out_html = build_html(selected, args.calendar_url, label, today, limit=args.limit)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out_html)
        shown = len(diversify(selected, args.limit))
        suffix = f" (showing {shown})" if shown < len(selected) else ""
        print(f"Wrote block for {label}: {len(selected)} hydro-relevant meetings"
              f"{suffix} -> {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(out_html + "\n")


if __name__ == "__main__":
    main()
