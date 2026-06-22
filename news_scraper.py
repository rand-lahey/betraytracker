#!/usr/bin/env python3
"""
BetrayTracker — FREE news scraper (GDELT)
=========================================
Tracks news mentions of a keyword (default "betrayal") using the GDELT DOC 2.0
API. GDELT is completely free: no API key, no signup, no quota to worry about.
It monitors worldwide online news in 100+ languages and updates every 15 min.

This is a drop-in, no-cost replacement for the *News* portion of BetrayTracker.
It writes the same data.js / data.json the dashboard (index.html) already reads,
so the existing dashboard works unchanged — the Reddit and X columns will simply
read 0 until you wire up those sources.

By default it tracks the word family betrayal / betray / betrayed / betrayer /
betraying (matched as OR) and restricts to English-language news.

Usage
-----
  python3 news_scraper.py                       # default: betrayal family, English
  python3 news_scraper.py --keywords war wars    # track different words
  python3 news_scraper.py --lang any             # all languages
  python3 news_scraper.py --timespan 1d          # counting window (default 1d)
  python3 news_scraper.py --max-history 1000

Only needs Python 3 + the `requests` package:
  pip install requests
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_JSON = HERE / "data.json"
DATA_JS = HERE / "data.js"

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
PLATFORM_LABELS = {"reddit": "Reddit", "twitter": "X / Twitter", "news": "News"}


def gdelt_get(params, retries=3):
    """Call a GDELT DOC API mode and return parsed JSON (or {} on failure).

    GDELT rate-limits rapid successive calls with HTTP 429; we back off and
    retry so volume counts stay accurate.
    """
    url = GDELT + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "BetrayTracker/1.0"})
    raw = ""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode("utf-8", "replace").strip()
            break
        except urllib.error.HTTPError as e:
            # 429 = rate limited. Back off briefly, but never hang for minutes.
            if e.code == 429 and attempt < retries - 1:
                time.sleep(3)
                continue
            print(f"  GDELT request failed: {e}", file=sys.stderr)
            return {}
        except Exception as e:  # noqa: BLE001
            print(f"  GDELT request failed: {e}", file=sys.stderr)
            return {}
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # GDELT occasionally returns an HTML error/notice page instead of JSON
        print(f"  GDELT returned non-JSON: {raw[:120]!r}", file=sys.stderr)
        return {}


def build_query(keywords, lang):
    """OR the keyword variants together and optionally restrict by language."""
    terms = " OR ".join(keywords)
    query = f"({terms})" if len(keywords) > 1 else terms
    if lang and lang.lower() != "any":
        query += f" sourcelang:{lang}"
    return query


def fetch_news(query, timespan):
    """Return (count, recent_items) of news articles matching the query."""

    # 1) Volume timeline -> use it to derive a count for the window.
    tl = gdelt_get({
        "query": query, "mode": "timelinevolraw",
        "format": "json", "timespan": timespan, "sort": "datedesc",
    })
    count = 0
    series = (tl.get("timeline") or [{}])
    if series and series[0].get("data"):
        # timelinevolraw returns absolute article counts per 15-min bucket
        count = int(sum(p.get("value", 0) for p in series[0]["data"]))

    time.sleep(1.5)  # be polite between GDELT calls to avoid rate limiting

    # 2) Article list -> recent examples for the feed.
    al = gdelt_get({
        "query": query, "mode": "artlist",
        "maxrecords": 25, "format": "json",
        "timespan": timespan, "sort": "datedesc",
    })
    arts = al.get("articles") or []
    if count == 0:
        count = len(arts)  # fallback if the timeline call returned nothing

    recent = []
    for a in arts[:12]:
        recent.append({
            "platform": "News",
            "title": (a.get("title") or "").strip()[:200],
            "url": a.get("url") or "",
            "snippet": (a.get("domain") or "") +
                       ((" · " + a.get("seendate", "")) if a.get("seendate") else ""),
        })
    return count, recent


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
    ap = argparse.ArgumentParser(description="Free GDELT news scraper for BetrayTracker")
    ap.add_argument("--keywords", nargs="+",
                    default=["betrayal", "betray", "betrayed", "betrayer", "betraying"],
                    help="word variants to track (matched as OR)")
    ap.add_argument("--lang", default="english",
                    help="source language filter, e.g. english; use 'any' to disable")
    ap.add_argument("--timespan", default="1d",
                    help="GDELT timespan window, e.g. 15min, 1h, 1d, 1w")
    ap.add_argument("--max-history", type=int, default=1000)
    args = ap.parse_args()

    keyword_display = " / ".join(args.keywords)
    query = build_query(args.keywords, args.lang)
    lang_note = "" if args.lang.lower() == "any" else f" [{args.lang} only]"
    print(f"Scanning GDELT news for '{keyword_display}'{lang_note} over the last {args.timespan} ...")
    count, recent = fetch_news(query, args.timespan)
    print(f"  News         {count} mentions")

    now = datetime.now(timezone.utc).isoformat()
    counts = {"reddit": 0, "twitter": 0, "news": count}

    state = load_history()
    # Start a clean history if this is sample data or the tracked terms changed
    # (old counts aren't comparable to a new keyword set / language filter).
    if state.get("sample") or state.get("keyword") != keyword_display:
        state = {"keyword": keyword_display, "history": [], "latest": None}
    state["keyword"] = keyword_display
    state["history"].append({"t": now, "counts": counts, "total": count})
    state["history"] = state["history"][-args.max_history:]
    state["latest"] = {
        "t": now, "counts": counts, "total": count,
        "platform_labels": PLATFORM_LABELS, "recent": recent,
    }
    state["generated_at"] = now
    state.pop("sample", None)

    write_outputs(state)
    print(f"\nWrote {DATA_JSON.name} / {DATA_JS.name}. "
          f"History now has {len(state['history'])} snapshot(s). Open index.html.")


if __name__ == "__main__":
    main()
