#!/usr/bin/env python3
"""
BetrayTracker — FREE news scraper
=================================
Tracks news mentions of a word family (default: betrayal / betray / betrayed /
betrayer / betraying) in English-language news. No API key, no signup.

Sources, in order of preference:
  1. Google News RSS  — primary news source. Free, no key, and (being a reader
     feed) holds up well on shared IPs such as GitHub Actions runners.
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
import re
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
# Heuristic: how many people did a headline say were involved?
# Not exact — if a title cites a number tied to a group of people, we use it.
# ---------------------------------------------------------------------------

# Nouns that denote groups of people who could be betrayed / involved.
_NOUNS = (r"people|persons?|victims?|employees?|workers?|staff|members?|fans?|"
          r"customers?|users?|soldiers?|troops|residents?|families|women|men|"
          r"children|kids|students?|players?|voters?|citizens?|patients?|"
          r"investors?|depositors?|passengers?|survivors?|refugees?|migrants?|"
          r"hostages?|prisoners?|inmates?|colleagues?|friends?|partners?|"
          r"teammates?|allies|supporters?|followers?|shareholders?|nurses?|"
          r"doctors?|officers?|veterans?|seniors?|tenants?|homeowners?|"
          r"officials?|executives?|directors?|lawmakers?|taxpayers?|locals|"
          r"subscribers?|clients?|constituents?|generals?|recruits?|pensioners?|"
          r"savers?|policyholders?|creditors?|landlords?|teachers?|villagers?|"
          r"protesters?")

# Topics to keep OUT of the tracker (sexual violence / child abuse). Murder and
# other violence are fine.
_BLOCK = re.compile(
    r"\b(rape|raping|rapist|sexual\s+assault|sexual\s+abuse|molest|molestation|"
    r"pedophile|paedophile|child\s+abuse|child\s+sex|child\s+sexual|csam|incest|"
    r"underage|statutory\s+rape)", re.I)


def is_blocked(text):
    return bool(_BLOCK.search(text or ""))
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
             "twelve": 12, "dozen": 12, "hundred": 100, "thousand": 1000}
_SCALE = {"hundred": 100, "thousand": 1000, "million": 1_000_000,
          "billion": 1_000_000_000}
_DIGIT_RE = re.compile(r"(\d[\d,]*)\s+((?:\w+\s+){0,2}?)(" + _NOUNS + r")\b")
_WORD_RE = re.compile(r"\b(" + "|".join(_WORD_NUM) + r")\s+((?:\w+\s+){0,2}?)("
                      + _NOUNS + r")\b")


def _apply_scale(num, gap):
    for w, mult in _SCALE.items():
        if re.search(r"\b" + w + r"\b", gap):
            return num * mult
    return num


def extract_people_count(text):
    """Best-effort head count from a headline. Returns 0 if none is mentioned."""
    t = (text or "").lower()
    best = 0
    for m in _DIGIT_RE.finditer(t):
        try:
            num = int(m.group(1).replace(",", ""))
        except ValueError:
            continue
        best = max(best, _apply_scale(num, m.group(2)))
    for m in _WORD_RE.finditer(t):
        best = max(best, _apply_scale(_WORD_NUM[m.group(1)], m.group(2)))
    return best


MAX_RECENT = 100  # how many articles to keep for the feed / head-count scan


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
    for it in items:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = (it.findtext("description") or "")
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        pub = (it.findtext("pubDate") or "").strip()
        # Google News appends " - Source" to titles; trim it for cleanliness.
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)]
        if is_blocked(title + " " + desc):
            continue  # skip sexual-violence / child-abuse topics
        recent.append({
            "platform": "News",
            "title": title[:200],
            "url": link,
            "snippet": (source + (" · " + pub if pub else "")).strip()[:200],
            "people": extract_people_count(title + " " + desc),
        })
    return len(recent), recent[:MAX_RECENT], True


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

    arts = [a for a in (data.get("articles") or [])
            if not is_blocked(a.get("title") or "")]
    recent = [{
        "platform": "News",
        "title": (a.get("title") or "").strip()[:200],
        "url": a.get("url") or "",
        "snippet": (a.get("domain") or "") +
                   ((" · " + a.get("seendate", "")) if a.get("seendate") else ""),
        "people": extract_people_count(a.get("title")),
    } for a in arts[:MAX_RECENT]]
    return len(arts), recent, True


def fetch_news(keywords, lang, timespan):
    """Try Google News RSS first, fall back to GDELT. Returns (count, recent, ok, source)."""
    count, recent, ok = gnews_fetch(keywords, timespan)
    if ok:
        return count, recent, True, "Google News"
    print("  Google News unavailable — falling back to GDELT ...", file=sys.stderr)
    count, recent, ok = gdelt_fetch(keywords, lang, timespan)
    return count, recent, ok, "GDELT"


def _parse_gdelt_date(s):
    return (datetime.strptime(s, "%Y%m%dT%H%M%SZ")
            .replace(tzinfo=timezone.utc).isoformat())


def gdelt_timeline(keywords, lang, timespan="1d"):
    """Fetch the full volume curve over `timespan` as 15-minute buckets.

    Returns a ready-to-use history list (one point per bucket) so the dashboard
    always shows a complete 24-hour window, even on the very first run — rather
    than slowly accumulating one point per scrape. Returns None on failure.

    Each point also carries per_hour = a trailing ~1-hour count, which gives the
    Betrayometer a real 24h distribution to compute its percentile against.
    """
    q = or_query(keywords)
    if lang and lang.lower() != "any":
        q += f" sourcelang:{lang}"
    url = GDELT + "?" + urllib.parse.urlencode({
        "query": q, "mode": "timelinevolraw", "format": "json", "timespan": timespan,
    })
    raw = http_get(url)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    series = data.get("timeline") or []
    pts = series[0].get("data") if series else None
    if not pts:
        return None

    vals = [int(p.get("value", 0)) for p in pts]
    hist = []
    for i, p in enumerate(pts):
        trailing = sum(vals[max(0, i - 3):i + 1])  # ~last hour (4×15min buckets)
        hist.append({
            "t": _parse_gdelt_date(p["date"]),
            "counts": {"reddit": 0, "twitter": 0, "news": vals[i]},
            "total": vals[i],
            "per_hour": trailing,
        })
    return hist


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
    print(f"Scanning English news for '{keyword_display}' (last 24h) ...")

    # 1) Current articles for the live feed + head-count (Google News, reliable).
    count_now, recent, ok, source = fetch_news(args.keywords, args.lang, args.timespan)
    if not ok:
        print("  All news sources failed (likely rate limited) — keeping previous "
              "data, not recording a snapshot this run.", file=sys.stderr)
        sys.exit(1)
    betrayed_total = sum(r.get("people", 0) for r in recent)
    articles_with_count = sum(1 for r in recent if r.get("people", 0) > 0)

    # 2) Full 24-hour volume curve in one call, so the chart always shows 24h.
    timeline = gdelt_timeline(args.keywords, args.lang, "1d")

    now = datetime.now(timezone.utc).isoformat()
    state = load_history()
    if state.get("sample") or state.get("keyword") != keyword_display:
        state = {"keyword": keyword_display, "history": [], "latest": None}
    state.pop("sample", None)
    state["keyword"] = keyword_display

    if timeline:
        history = timeline
        total = sum(p["total"] for p in history)
        per_hour = round(total / 24, 1)
        current_rate = history[-1].get("per_hour", per_hour)
        history[-1]["betrayed_total"] = betrayed_total
        history[-1]["articles_with_count"] = articles_with_count
        src = f"GDELT 24h timeline + {source}"
    else:
        # Fallback: accumulate Google News snapshots over time (no instant 24h).
        total = count_now
        per_hour = round(total / 24, 1)
        current_rate = per_hour
        history = state.get("history") or []
        history.append({
            "t": now, "counts": {"reddit": 0, "twitter": 0, "news": total},
            "total": total, "per_hour": per_hour,
            "betrayed_total": betrayed_total, "articles_with_count": articles_with_count,
        })
        src = source

    state["history"] = history[-args.max_history:]
    state["latest"] = {
        "t": now, "counts": {"reddit": 0, "twitter": 0, "news": total}, "total": total,
        "platform_labels": PLATFORM_LABELS, "recent": recent, "source": src,
        "per_hour": per_hour, "current_rate": current_rate,
        "betrayed_total": betrayed_total, "articles_with_count": articles_with_count,
    }
    state["generated_at"] = now
    write_outputs(state)
    print(f"  {src}: {total} betrayals/24h · ~{per_hour}/hr avg · {current_rate}/hr now · "
          f"est. {betrayed_total} betrayed · {len(state['history'])} data points")


if __name__ == "__main__":
    main()
