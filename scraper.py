#!/usr/bin/env python3
"""
BetrayTracker scraper
=====================
Scans social media + news for live mentions of the word "betrayal" using the
Bright Data CLI (`bdata`), then writes the results to data.js / data.json which
the dashboard (index.html) reads.

Platforms tracked: Reddit, X/Twitter, News.

Each run takes a fresh "snapshot" (a count of current mentions per platform +
a sample of the most recent mention texts) and APPENDS it to a rolling history
so the dashboard can draw a time-series graph. Run it on a schedule (cron / the
Cowork scheduler) to build up a live tracker.

Requirements
------------
  1. Node.js >= 20
  2. Bright Data CLI:  curl -fsSL https://cli.brightdata.com/install.sh | bash
  3. Auth, either:
       bdata login                      (one-time OAuth)
       export BRIGHTDATA_API_KEY=...    (non-interactive)

Usage
-----
  python3 scraper.py                # one snapshot, append to history
  python3 scraper.py --keyword war  # track a different word
  python3 scraper.py --max-history 500
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_JSON = HERE / "data.json"
DATA_JS = HERE / "data.js"

# Where to look. Each platform is a Google search scoped with a site: filter,
# run through the Bright Data SERP API so it isn't blocked.
PLATFORMS = {
    "reddit":  {"label": "Reddit",     "site": "reddit.com"},
    "twitter": {"label": "X / Twitter", "site": "x.com OR site:twitter.com"},
    "news":    {"label": "News",        "site": None},  # uses --type news
}


def find_bdata():
    """Locate the Bright Data CLI binary."""
    for name in ("bdata", "brightdata"):
        path = shutil.which(name)
        if path:
            return path
    return None


def run_search(bdata, keyword, platform_key, cfg):
    """Run one Bright Data SERP search and return (count, recent_items)."""
    if platform_key == "news":
        query = f'"{keyword}"'
        cmd = [bdata, "search", query, "--type", "news", "--json"]
    else:
        query = f'"{keyword}" site:{cfg["site"]}'
        cmd = [bdata, "search", query, "--json"]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        print(f"  [{platform_key}] timed out", file=sys.stderr)
        return 0, []
    except Exception as e:  # noqa: BLE001
        print(f"  [{platform_key}] error: {e}", file=sys.stderr)
        return 0, []

    if proc.returncode != 0:
        print(f"  [{platform_key}] CLI error: {proc.stderr.strip()[:200]}", file=sys.stderr)
        return 0, []

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"  [{platform_key}] could not parse JSON output", file=sys.stderr)
        return 0, []

    # Bright Data SERP JSON exposes organic results (key varies a little by
    # engine/version), and sometimes a total results estimate.
    organic = (
        data.get("organic")
        or data.get("organic_results")
        or data.get("results")
        or []
    )

    # Prefer Google's reported total-results estimate when present; otherwise
    # fall back to the number of results on the page.
    total = None
    for k in ("results_cnt", "total_results", "search_information"):
        v = data.get(k)
        if isinstance(v, dict):
            v = v.get("total_results") or v.get("results_cnt")
        if isinstance(v, (int, float)) and v > 0:
            total = int(v)
            break
    count = total if total is not None else len(organic)

    recent = []
    for item in organic[:8]:
        recent.append({
            "title": (item.get("title") or item.get("name") or "").strip()[:200],
            "url": item.get("link") or item.get("url") or "",
            "snippet": (item.get("description") or item.get("snippet") or "").strip()[:280],
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
    # data.js lets the dashboard load over file:// without CORS issues.
    DATA_JS.write_text("window.BETRAYAL_DATA = " + json.dumps(payload) + ";\n")


def main():
    ap = argparse.ArgumentParser(description="BetrayTracker social-media scraper")
    ap.add_argument("--keyword", default="betrayal", help="word to track")
    ap.add_argument("--max-history", type=int, default=1000,
                    help="max snapshots to keep")
    args = ap.parse_args()

    bdata = find_bdata()
    if not bdata:
        print("ERROR: Bright Data CLI not found.\n"
              "Install it with:\n"
              "  curl -fsSL https://cli.brightdata.com/install.sh | bash\n"
              "or:\n"
              "  npm install -g @brightdata/cli\n"
              "then authenticate with `bdata login` "
              "or set BRIGHTDATA_API_KEY.", file=sys.stderr)
        sys.exit(2)

    keyword = args.keyword
    print(f"Scanning for '{keyword}' across {', '.join(p['label'] for p in PLATFORMS.values())} ...")

    counts = {}
    recent_all = []
    for key, cfg in PLATFORMS.items():
        count, recent = run_search(bdata, keyword, key, cfg)
        counts[key] = count
        print(f"  {cfg['label']:<12} {count} mentions")
        for r in recent:
            r["platform"] = cfg["label"]
            recent_all.append(r)

    now = datetime.now(timezone.utc).isoformat()
    snapshot = {"t": now, "counts": counts, "total": sum(counts.values())}

    state = load_history()
    state["keyword"] = keyword
    state["history"].append(snapshot)
    state["history"] = state["history"][-args.max_history:]
    state["latest"] = {
        "t": now,
        "counts": counts,
        "total": snapshot["total"],
        "platform_labels": {k: v["label"] for k, v in PLATFORMS.items()},
        "recent": recent_all[:30],
    }
    state["generated_at"] = now

    write_outputs(state)
    print(f"\nTotal: {snapshot['total']} mentions. "
          f"History now has {len(state['history'])} snapshot(s).")
    print(f"Wrote {DATA_JSON.name} and {DATA_JS.name}. Open index.html to view.")


if __name__ == "__main__":
    main()
