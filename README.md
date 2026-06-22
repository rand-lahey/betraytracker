# BetrayTracker

A live dashboard that tracks the word **"betrayal"** (and betray / betrayed /
betrayer / betraying) in English news. It shows a 24-hour counter, betrayals per
hour, an estimated head-count of people betrayed, a **Betrayometer** gauge, a
24-hour chart, and a feed of recent stories. Live at **https://betrayals.ca**.

## Architecture

Everything runs on a single **Cloudflare Worker** — no servers, no git commits
for data, no build limits.

```
BetrayTracker/
├── worker.js        ← the Worker: cron scraper + server
├── wrangler.jsonc   ← Worker config (assets, KV binding, cron)
├── index.html       ← the dashboard (served as a static asset)
├── .assetsignore    ← keeps non-static files out of the asset bundle
└── README.md
```

- `scheduled()` runs hourly on a Cloudflare cron. It pulls several pages from
  the GNews API (≈40 stories) for the last 24h, computes the metrics, appends a
  point to a rolling 24-hour history, and stores it all in a KV namespace.
  (More stories per run = more requests against GNews' 100/day free cap, hence
  the hourly cadence. Tune `PAGES` in `worker.js` and the cron together.)
- `fetch()` serves `index.html` and answers `/data.json` from KV (building it
  on demand if KV is still empty). The page polls `/data.json` every 60s.

**Why GNews?** The obvious free sources block Cloudflare's shared server IPs —
Google News returns `503`, GDELT returns `429` — so a Worker can't scrape them
directly. GNews is an authenticated API that works reliably from a Worker. Its
free tier allows 100 requests/day, which is why the cron runs every 15 minutes
(96/day, safely under the cap).

## One-time deploy

1. **Get a GNews API key** — sign up free at https://gnews.io/register.
2. **Create a KV namespace** — Cloudflare → **Storage & Databases → KV → Create
   namespace** (`betraytracker`). Copy the **ID** into `wrangler.jsonc`.
3. **Deploy the Worker from the repo** — **Workers & Pages** → create a
   **Worker** connected to this GitHub repo. Cloudflare reads `wrangler.jsonc`
   (assets + KV binding + cron). It must be a **Worker**, not a Pages project
   (Pages can't run cron).
4. **Add the key as a secret** — Worker → **Settings → Variables and Secrets →
   Add → Secret**, name `GNEWS_KEY`, value = your key. Save and redeploy.
5. **Add the domain** — Worker → **Settings → Domains & Routes** → add
   `betrayals.ca` (and `www.betrayals.ca`). Cloudflare handles DNS + SSL.
6. Visit the site — the chart fills into a complete 24-hour curve over the
   first day as the cron adds a point every 15 minutes.

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
