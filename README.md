# BetrayTracker

A live dashboard that scans social media + news for mentions of the word
**"betrayal"** and shows a real-time counter, a time-series graph, and a feed of
recent mentions across **Reddit, X/Twitter, and News**.

```
BetrayTracker/
├── index.html    ← the dashboard (open this)
├── scraper.py    ← collects real mentions via Bright Data, writes data.js/json
├── data.js       ← latest data (currently SAMPLE data so the dashboard renders)
├── data.json     ← same data, for HTTP-served auto-refresh
└── README.md
```

## Two ways to get data

| Scraper | Cost | Covers | Needs |
|---|---|---|---|
| `news_scraper.py` | **Free, no key** | News only (via GDELT) | `pip install requests` |
| `scraper.py` | Paid infra | Reddit + X/Twitter + News | Bright Data API key |

If you only need **news**, use the free one:

```bash
pip install requests
python3 news_scraper.py
```

It uses the [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)
— completely free, no signup, no API key, updated every 15 minutes. It writes
the same `data.js`/`data.json` the dashboard reads (Reddit and X columns just
stay at 0).

**By default it tracks the whole word family — betrayal, betray, betrayed,
betrayer, betraying — and only English-language news.** Options:

```bash
python3 news_scraper.py --keywords war wars   # track different words
python3 news_scraper.py --lang any            # all languages, not just English
python3 news_scraper.py --timespan 1d         # counting window: 15min/1h/1d/1w
```

Changing the tracked words or language automatically starts a fresh history
(old counts aren't comparable to a new query).

## Quick look

Just open `index.html` in your browser. It loads immediately with **sample
data** so you can see the layout. The banner at the top reminds you it's sample
data until you run a real scan.

## Going live (real data)

Real social-media data comes from **Bright Data**, which handles bot-blocking,
CAPTCHAs, and rate limits for you. One-time setup:

**1. Install the Bright Data CLI** (needs Node.js 20+)

```bash
curl -fsSL https://cli.brightdata.com/install.sh | bash
# or:  npm install -g @brightdata/cli
```

**2. Authenticate** (either option works)

```bash
bdata login                      # opens browser for OAuth, saves the key
# or, non-interactive:
export BRIGHTDATA_API_KEY=your_key_here
```

You can create a free account / get an API key at https://brightdata.com.

**3. Run a scan**

```bash
cd BetrayTracker
python3 scraper.py
```

Each run takes a snapshot (mention counts per platform + recent examples) and
**appends** it to `data.json`/`data.js`. Refresh `index.html` to see it.

## Live auto-refresh

The dashboard auto-refreshes every 60 seconds. For that to pull new data, serve
the folder over HTTP (opening via `file://` only loads the embedded snapshot):

```bash
cd BetrayTracker
python3 -m http.server 8000
# then open http://localhost:8000
```

## Make it a *continuous* tracker

Run the scraper on a schedule so the graph fills in over time.

**cron (every 30 min):**
```
*/30 * * * * cd /path/to/BetrayTracker && /usr/bin/python3 scraper.py >> scrape.log 2>&1
```

You can also ask Claude in Cowork to schedule it for you ("run the BetrayTracker
scraper every 30 minutes").

## Options

```bash
python3 scraper.py --keyword betrayal     # track a different word
python3 scraper.py --max-history 500       # cap stored snapshots
```

## Notes

- Counts use Google's reported total-results estimate per platform when
  available, falling back to results-on-page — so treat them as a **relative
  trend indicator**, not an exact census.
- "X / Twitter" is matched via `site:x.com OR site:twitter.com`; News uses the
  SERP news vertical.
- To reset to a clean slate, delete `data.json` and `data.js` and run the
  scraper again.
