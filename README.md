# BetrayTracker

A live dashboard that tracks the word **"betrayal"** (and betray / betrayed /
betrayer / betraying) in English news. It shows a 24-hour counter, betrayals per
hour, an estimated head-count of people betrayed, a **Betrayometer** gauge, a
24-hour chart, and a feed of recent stories. Live at **https://betrayals.ca**.

## Architecture

Scraping happens in **GitHub Actions** (the free news sources block Cloudflare's
IPs but work from GitHub's runners); the **Cloudflare Worker** just mirrors the
result and serves it.

```
BetrayTracker/
├── news_scraper.py            ← the scraper (Google News RSS + GDELT)
├── .github/workflows/scrape.yml ← runs the scraper, commits data.json
├── data.json                  ← scraper output (updated by the Action)
├── worker.js                  ← Worker: mirrors data.json + serves the site
├── wrangler.jsonc             ← Worker config (assets, KV binding, cron)
├── index.html                 ← the dashboard
├── .assetsignore              ← keeps non-static files out of the asset bundle
└── README.md
```

- **GitHub Action** (`scrape.yml`) runs every 15 min. It pulls up to ~100
  stories from Google News RSS plus GDELT's 24-hour volume curve, applies the
  content blocklist, computes the metrics, and publishes `data.json` to a
  dedicated **`data`** branch (force-pushed, single commit). Keeping data off
  `main` means pushing code never collides with data commits, and data commits
  never trigger Worker rebuilds.
- **Worker** (`worker.js`) mirrors `data.json` from the `data` branch into KV
  every 10 min and serves it at `/data.json`; the page polls that every 60s.

**Why split it this way?** Google News (503) and GDELT (429) block/throttle
Cloudflare's shared server IPs, so a Worker can't scrape them — but they work
from GitHub's runners. So GitHub scrapes (free, no key, ~100 stories) and the
Worker just relays the file.

## One-time deploy

1. **Create a KV namespace** — Cloudflare → **Storage & Databases → KV → Create
   namespace** (`betraytracker`). Copy the **ID** into `wrangler.jsonc`.
2. **Deploy the Worker from the repo** — **Workers & Pages** → create a
   **Worker** connected to this GitHub repo. It must be a **Worker**, not a
   Pages project (Pages can't run cron).
3. **Build branch** — make sure the Worker builds only from `main`. Because
   data lives on the separate `data` branch, data commits never touch `main`,
   so they can't trigger rebuilds. (No build-watch-path tweaks needed.)
4. **Add the domain** — Worker → **Settings → Domains & Routes** → add
   `betrayals.ca` (and `www.betrayals.ca`). Cloudflare handles DNS + SSL.
5. **Enable the Action** — the scraper runs automatically on its schedule once
   `scrape.yml` is pushed; trigger the first run from the **Actions** tab.

No API keys or secrets are required — everything uses free, keyless sources.

## Endpoints

- `/` — the dashboard
- `/data.json` — current state (served from KV)
- `/refresh` — force a rebuild now (uses one GNews request — don't spam it)
- `/debug` — shows the GNews HTTP status and whether the key is detected

## Dashboard

- **Betrayals (24h)** — matching articles in the last 24 hours
- **Betrayals / hour** — the 24h average
- **Total betrayed** — head counts parsed from story text ("12 employees", etc.)
- **Betrayometer** — needle = the current rate's percentile vs the last 24h
- Hover any stat box to swap the chart to that metric's 24h history.

Counts are a relative trend indicator, not an exact census.
