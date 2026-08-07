#!/usr/bin/env python3
"""
Why does nyiso.com serve the CI runner a committee page with no file browser?

NYISO dropped off the calendar on 2026-07-09. Every run since logged
"success, 0 events" because all 25 committee pages came back without the
three Liferay markers the scraper needs (authToken, plid, portletId). The
same pages parse fine from a workstation on any request shape, so the
difference is the environment, not the markup.

ANSWERED 2026-08-07 from run 31204346374: hypothesis D. Every one of 12
probes came back HTTP 202, 2451 bytes, `X-Amzn-Waf-Action: challenge`, from
egress 20.55.117.34 (Azure, where GitHub runners live). Kept and extended
because it is the tool for the next time this changes — a rule escalating
from challenge to captcha, or the block lifting, both show up here first.

Four explanations, and they need different fixes:

  A. Hard block on the runner IP (CloudFront/WAF rejecting the ASN).
     Signature: every variant fails, no WAF action header.
     Fix: different egress. No header change will help.

  D. AWS WAF Challenge  ← what is actually happening.
     Signature: HTTP 202, ~2.4 KB body, X-Amzn-Waf-Action: challenge,
     body running window.awsWafCookie JS. Not a refusal — WAF wants the
     client to execute JS and earn an aws-waf-token cookie.
     Fix: solve once in Playwright, hand the cookie to requests.
     If it ever reads `captcha`, headless Chromium will not clear it.

  B. CloudFront cache artifact — the runner's edge POP holds an anonymous
     copy rendered without a session, so the per-session authToken is
     absent. Signature: the no-cache / cache-buster variants succeed where
     the plain one fails, and X-Cache flips Hit -> Miss.
     Fix: a request header, cheap.

  C. Bot fingerprinting on headers rather than IP.
     Signature: the full browser-like header set succeeds where the
     scraper's two-header set fails.
     Fix: send the fuller header set.

So try all four request shapes against a few committees and print enough of
each response to tell the cases apart. Read-only: no DB, no writes, no
commits. Always exits 0 — this reports, it does not gate.
"""

import re
import sys
import time

import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from scrapers.nyiso_scraper import NYISOScraper as N  # noqa: E402

SLUGS = ["business-issues-committee-bic", "management-committee-mc", "icapwg"]

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# What the scraper actually sends today (BaseRTOScraper.__init__).
SCRAPER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# A real Chrome navigation, for hypothesis C.
FULL_BROWSER_HEADERS = {
    **SCRAPER_HEADERS,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Chromium";v="120", "Not(A:Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Connection": "keep-alive",
}

NO_CACHE_HEADERS = {
    **SCRAPER_HEADERS,
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

VARIANTS = [
    ("scraper-default", SCRAPER_HEADERS, False),
    ("no-cache-header", NO_CACHE_HEADERS, False),
    ("cache-buster-qs", SCRAPER_HEADERS, True),
    ("full-browser-hdrs", FULL_BROWSER_HEADERS, False),
]


def egress_ip():
    """Which IP does NYISO see? Correlates a block with the runner's ASN."""
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            return requests.get(url, timeout=15).text.strip()
        except Exception:
            continue
    return "(unavailable)"


def waf_action(resp, body):
    """AWS WAF's verdict on this request, if it had one.

    This is the distinction the first version of this script missed: it only
    counted successes, so a solvable JS challenge and an outright refusal
    both came out as "hard block". They need completely different fixes.
    """
    hdr = resp.headers.get("X-Amzn-Waf-Action", "").lower()
    if hdr:
        return hdr
    if "awsWafCookieDomainList" in body or "window.awsWafCookie" in body:
        return "challenge"
    return ""


def probe(slug, label, headers, bust):
    url = f"https://www.nyiso.com/{slug}"
    if bust:
        # Vary the query string so CloudFront cannot serve a cached object.
        url += f"?cb={int(time.time())}"
    s = requests.Session()
    s.headers.update(headers)
    try:
        r = s.get(url, timeout=30)
    except Exception as e:
        print(f"    {label:18} EXCEPTION {type(e).__name__}: {e}")
        return False, ""

    body = r.text
    marks = {
        "authToken": bool(N._AUTH_TOKEN_RE.search(body)),
        "plid": bool(N._PLID_RE.search(body)),
        "portletId": bool(N._PORTLET_RE.search(body)),
    }
    ok = all(marks.values())
    missing = ",".join(k for k, v in marks.items() if not v) or "-"
    waf = waf_action(r, body)

    print(f"    {label:18} HTTP {r.status_code}  {len(body):>7} bytes  "
          f"x-cache={r.headers.get('X-Cache', '-'):<22} "
          f"waf={waf or '-':<10} "
          f"handshake={'YES' if ok else 'no'}  missing={missing}")

    if not ok:
        # The body is the evidence: an Access Denied stub, a JS challenge and
        # a real-but-cached page look nothing alike.
        snippet = re.sub(r"\s+", " ", body[:300]).strip()
        print(f"      body[:300]: {snippet}")
        for h in ("X-Amz-Cf-Pop", "Via", "Cf-Ray", "X-Cache-Hits",
                  "Retry-After", "X-Amzn-Waf-Action"):
            if h in r.headers:
                print(f"      {h}: {r.headers[h]}")
    return ok, waf


def main():
    print("=" * 72)
    print("  NYISO handshake diagnostic")
    print("=" * 72)
    print(f"  egress IP as NYISO sees it: {egress_ip()}")
    print(f"  probing {len(SLUGS)} committees x {len(VARIANTS)} request shapes")
    print()

    wins = {label: 0 for label, _, _ in VARIANTS}
    actions = set()
    for slug in SLUGS:
        print(f"  /{slug}")
        for label, headers, bust in VARIANTS:
            got, waf = probe(slug, label, headers, bust)
            if got:
                wins[label] += 1
            if waf:
                actions.add(waf)
            time.sleep(1.5)
        print()

    print("=" * 72)
    print("  RESULT — handshakes obtained, out of "
          f"{len(SLUGS)} committees")
    for label, n in wins.items():
        print(f"    {label:18} {n}/{len(SLUGS)}")
    print()

    total = sum(wins.values())
    if actions:
        print(f"  AWS WAF action seen: {', '.join(sorted(actions))}")
        print()

    if total == 0 and "challenge" in actions:
        print("  VERDICT: hypothesis D — AWS WAF JS challenge. NOT a block:")
        print("  WAF is asking the client to run JavaScript and earn an")
        print("  aws-waf-token cookie. requests cannot, so it stores the stub.")
        print("  Fix: solve it once in Playwright and hand the cookie to the")
        print("  requests session (nyiso_scraper._solve_waf_challenge).")
        print("  If this shows 'captcha' instead, headless will not clear it.")
    elif total == 0:
        print("  VERDICT: hypothesis A — hard block on this IP. No request")
        print("  shape gets through and WAF is not asking for anything, so")
        print("  headers cannot fix it. Options are a different egress")
        print("  (self-hosted runner, proxy) or asking NYISO to allow it.")
    elif wins["scraper-default"] == len(SLUGS):
        print("  VERDICT: nothing reproduced here — the runner reached NYISO")
        print("  fine this time. Either the block lifted or it is")
        print("  intermittent; compare against the scrape_log history before")
        print("  concluding it is fixed.")
    elif wins["no-cache-header"] or wins["cache-buster-qs"]:
        print("  VERDICT: hypothesis B — CloudFront cache artifact. The edge")
        print("  POP was serving a session-less copy. Fix is a request header")
        print("  on the handshake GET; see which variant above won.")
    elif wins["full-browser-hdrs"]:
        print("  VERDICT: hypothesis C — header fingerprinting. Send the full")
        print("  browser header set on the handshake GET.")
    else:
        print("  VERDICT: mixed. Read the per-variant rows above.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
