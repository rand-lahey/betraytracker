# BetrayTracker

A live dashboard that scans the news for the word **"betrayal"** (and betray /
betrayed / betrayer / betraying) and shows a real-time counter, a 24-hour graph,
a Betrayometer, and a feed of recent stories. Live at **betrayals.ca**.

## How it runs (Cloudflare Worker)

The live site is powered entirely by a **Cloudflare Worker** (`worker.js`) — no
servers to manage, no git commits, no build limits:

- `scheduled()` runs every 15 minutes on a Cloudflare cron. It calls the GNews
  API for betrayal stories in the last 24h, computes the metrics, appends a
  point to a 24-hour rolling history, and stores it all in a KV namespace.
- `fetch()` serves the dashboard (`index.html`) and answers `/data.json` from
  KV. The page polls `/data.json` every 60s, so it's always current.

Why GNews and not GDELT/Google News directly? Those free sources block or
throttle Cloudflare's shared server IPs (Google News returns 503, GDELT 429),
so a Worker can't scrape them. GNews is an authenticated API that works from a
Worker. Its free tier allows 100 requests/day — hence the 15-minute cadence.

### One-time deploy

1. **Get a GNews API key.** Sign up free at https://gnews.io/register and copy
   your key from the dashboard.
2. **Create a KV namespace.** Cloudflare → **Storage & Databases → KV → Create
   namespace**, name it `betraytracker`. Copy the **ID** into `wrangler.jsonc`
   (replace `PASTE_YOUR_KV_NAMESPACE_ID_HERE`).
3. **Deploy the Worker from the repo.** In **Workers & Pages**, create a
   **Worker** connected to this GitHub repo (Workers Builds). Cloudflare reads
   `wrangler.jsonc` and deploys with the assets, KV binding, and cron trigger.
   (A plain Pages project can't do cron — it must be a Worker.)
4. **Add the API key as a secret.** Worker → **Settings → Variables and Secrets
   → Add → Secret**, name `GNEWS_KEY`, value = your GNews key. Save and redeploy.
5. **Add the domain.** Worker → **Settings → Domains & Routes**, add
   `betrayals.ca` (and `www.betrayals.ca`). Cloudflare manages the DNS + SSL.
6. Visit `betrayals.ca` — the first load builds the data on demand; the cron
   then refreshes every 15 minutes and the chart fills out over the first day.

The old GitHub Action (`.github/workflows/scrape.yml`) is **disabled** — the
Worker replaces it. The Python scrapers below are now optional / for local use.

---

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
| `news_scraper.py` | **Free, no key** | News only (via GDELT) | Python 3 only |
| `scraper.py` | Paid infra | Reddit + X/Twitter + News | Bright Data API key |

If you only need **news**, use the free one (no dependencies to install — it
uses Python's standard library):

```bash
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

## Hosting it live on GitHub Pages

The repo includes `.github/workflows/scrape.yml`, a GitHub Action that runs the
free news scraper every 5 minutes **on GitHub's servers** and commits the
updated data back to the repo. That keeps the GitHub Pages dashboard fresh even
when your computer is off — no API key required. (GitHub's scheduler is
best-effort and often delays runs under load, so treat "every 5 minutes" as a
target, not a guarantee.)

To turn it on after pushing the repo:

1. **Settings → Pages** → set Source to "Deploy from a branch", branch `main`,
   folder `/ (root)`. Your dashboard appears at
   `https://USERNAME.github.io/BetrayTracker/`.
2. **Settings → Actions → General** → under "Workflow permissions" select
   **Read and write permissions** (so the Action can commit new data), then Save.
3. **Actions** tab → open "Scrape betrayal mentions" → **Run workflow** to do a
   first run immediately instead of waiting for the schedule.

Note: GitHub disables scheduled Actions after ~60 days of no repo activity, and
may delay scheduled runs slightly under load.

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
