#!/usr/bin/env python3
"""
BetrayTracker — FREE news scraper
=================================
Tracks news mentions of a word family (default: betrayal / betray / betrayed /
betrayer / betraying) in English-language news. No API key, no signup.

Sources, in order of preference:
  1. Google News RSS  — primary. Free, no key, and (being a reader feed) holds
     up well on shared IPs such as GitHub Actions runners.
  2. GDELT DOC 2.0 API — automatic fallback if Google News is unavailable.
     GDELT is also free but its API rate-limits shared IPs aggressively, which
     is why it's the fallback rather than the primary.

It writes the same data.js / data.json the dashboard (index.html) reads, so the
dashboard works unchanged — Reddit and X columns stay at 0 until wired up.

Each run appends one timestamped snapshot (mention count + recent examples) so
the dashboard can draw a time series. Run it on a schedule for a live tracker.

Usage
-----
  python3 news_scraper.py                       # default: betrayal family, English
  python3 news_scraper.py --keywords war wars    # track different words
  python3 news_scraper.py --timespan 1d          # window: 1h / 1d / 1w
  python3 news_scraper.py --max-history 1000

Needs only Python 3 (standard library — no pip installs).
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_JSON = HERE / "data.json"
DATA_JS = HERE / "data.js"

GNEWS = "https://news.google.com/rss/search"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
PLATFORM_LABELS = {"reddit": "Reddit", "twitter": "X / Twitter", "news": "News"}
UA = "Mozilla/5.0 (compatible; BetrayTracker/1.0)"


def http_get(url, retries=3, timeout=25):
    """GET a URL, returning the decoded body or None on failure (with backoff)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(3)
                continue
            print(f"  HTTP {e.code} from {url.split('?')[0]}", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001
            print(f"  request failed: {e}", file=sys.stderr)
            return None
    return None


def or_query(keywords):
    terms = " OR ".join(keywords)
    return f"({terms})" if len(keywords) > 1 else terms


# ---------------------------------------------------------------------------
# Source 1: Google News RSS  (primary)
# ---------------------------------------------------------------------------

def _gnews_when(timespan):
    return {"15min": "1h", "1h": "1h", "1d": "1d", "1w": "7d",
            "7d": "7d", "1m": "30d"}.get(timespan, "1d")


def gnews_fetch(keywords, timespan):
    """Return (count, recent, ok) from the Google News RSS search feed (English)."""
    q = or_query(keywords) + f" when:{_gnews_when(timespan)}"
    url = GNEWS + "?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    raw = http_get(url)
    if not raw:
        return 0, [], False
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"  Google News RSS parse error: {e}", file=sys.stderr)
        return 0, [], False

    items = root.findall(".//channel/item")
    recent = []
    for it in items[:12]:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        pub = (it.findtext("pubDate") or "").strip()
        # Google News appends " - Source" to titles; trim it for cleanliness.
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)]
        recent.append({
            "platform": "News",
            "title": title[:200],
            "url": link,
            "snippet": (source + (" · " + pub if pub else "")).strip()[:200],
        })
    return len(items), recent, True


# ---------------------------------------------------------------------------
# Source 2: GDELT DOC 2.0 API  (fallback)
# ---------------------------------------------------------------------------

def gdelt_fetch(keywords, lang, timespan):
    """Return (count, recent, ok) from a single GDELT article-list call."""
    q = or_query(keywords)
    if lang and lang.lower() != "any":
        q += f" sourcelang:{lang}"
    url = GDELT + "?" + urllib.parse.urlencode({
        "query": q, "mode": "artlist", "maxrecords": 250,
        "format": "json", "timespan": timespan, "sort": "datedesc",
    })
    raw = http_get(url)
    if not raw:
        return 0, [], False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"  GDELT returned non-JSON: {raw[:120]!r}", file=sys.stderr)
        return 0, [], False
    if "articles" not in data:
        return 0, [], False

    arts = data.get("articles") or []
    recent = [{
        "platform": "News",
        "title": (a.get("title") or "").strip()[:200],
        "url": a.get("url") or "",
        "snippet": (a.get("domain") or "") +
                   ((" · " + a.get("seendate", "")) if a.get("seendate") else ""),
    } for a in arts[:12]]
    return len(arts), recent, True


def fetch_news(keywords, lang, timespan):
    """Try Google News RSS first, fall back to GDELT. Returns (count, recent, ok, source)."""
    count, recent, ok = gnews_fetch(keywords, timespan)
    if ok:
        return count, recent, True, "Google News"
    print("  Google News unavailable — falling back to GDELT ...", file=sys.stderr)
    count, recent, ok = gdelt_fetch(keywords, lang, timespan)
    return count, recent, ok, "GDELT"


# ---------------------------------------------------------------------------

def load_history():
    if DATA_JSON.exists():
        try:
            return json.loads(DATA_JSON.read_text())
        except json.JSONDecodeError:
            pass
    return {"keyword": "betrayal", "history": [], "latest": None}


def write_outputs(payload):
    DATA_JSON.write_text(json.dumps(payload, indent=2))
    DATA_JS.write_text("window.BETRAYAL_DATA = " + json.dumps(payload) + ";\n")


def main():
    ap = argparse.ArgumentParser(description="Free news scraper for BetrayTracker")
    ap.add_argument("--keywords", nargs="+",
                    default=["betrayal", "betray", "betrayed", "betrayer", "betraying"],
                    help="word variants to track (matched as OR)")
    ap.add_argument("--lang", default="english",
                    help="GDELT fallback language filter; 'any' to disable")
    ap.add_argument("--timespan", default="1d",
                    help="counting window: 1h, 1d, or 1w")
    ap.add_argument("--max-history", type=int, default=1000)
    args = ap.parse_args()

    keyword_display = " / ".join(args.keywords)
    print(f"Scanning English news for '{keyword_display}' over the last {args.timespan} ...")
    count, recent, ok, source = fetch_news(args.keywords, args.lang, args.timespan)
    if not ok:
        print("  All news sources failed (likely rate limited) — keeping previous "
              "data, not recording a snapshot this run.", file=sys.stderr)
        sys.exit(1)
    print(f"  News ({source}): {count} mentions, {len(recent)} recent examples")

    now = datetime.now(timezone.utc).isoformat()
    counts = {"reddit": 0, "twitter": 0, "news": count}

    state = load_history()
    # Reset history if this is sample data or the tracked terms changed.
    if state.get("sample") or state.get("keyword") != keyword_display:
        state = {"keyword": keyword_display, "history": [], "latest": None}
    state["keyword"] = keyword_display
    state["history"].append({"t": now, "counts": counts, "total": count})
    state["history"] = state["history"][-args.max_history:]
    state["latest"] = {
        "t": now, "counts": counts, "total": count,
        "platform_labels": PLATFORM_LABELS, "recent": recent,
        "source": source,
    }
    state["generated_at"] = now
    state.pop("sample", None)

    write_outputs(state)
    print(f"\nWrote {DATA_JSON.name} / {DATA_JS.name}. "
          f"History now has {len(state['history'])} snapshot(s). Open index.html.")


if __name__ == "__main__":
    main()
