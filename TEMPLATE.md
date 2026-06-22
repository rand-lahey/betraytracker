# Make your own news tracker from this template

BetrayTracker is built so you can clone it and re-point it at **any** keyword /
topic. The architecture is reusable as-is:

```
GitHub Action (news_scraper.py)  →  commits data.json  →  Cloudflare Worker mirrors + serves it  →  your domain
```

Free, no API keys. To spin up a new tracker (say, a "Scandal Tracker" or
"Layoffs Tracker"), copy this repo into a new GitHub repo and change the values
below. Each is a small, clearly-marked edit.

---

## 1. Pick your keywords — `news_scraper.py`

Change the default keyword list (the `--keywords` argument). These are OR-matched
in the news search:

```python
ap.add_argument("--keywords", nargs="+",
                default=["betrayal", "betray", "betrayed", "betrayer", "betraying"],
```

Tips: use 3–6 distinctive word variants of one concept. Avoid very common words
(they'll match everything).

## 2. (Optional) Adjust the content blocklist — `news_scraper.py`

`_BLOCK` filters out topics you never want shown (here: sexual violence / child
abuse). Edit the list for your topic, or leave it as a sensible safety default:

```python
_BLOCK = re.compile(r"\b(rape|raping|rapist|sexual\s+assault|...)", re.I)
```

The head-count word list `_NOUNS` (used for the "Total betrayed"-style stat)
can also be tweaked, but the defaults cover most "N people" phrasings.

## 3. Point the Worker at your repo — `worker.js`

Change `DATA_URL` to your new repo (owner/name), and the placeholder keyword in
`EMPTY`:

```js
const DATA_URL =
  "https://raw.githubusercontent.com/YOUR-USER/YOUR-REPO/main/data.json";
```

## 4. Name the Worker + KV — `wrangler.jsonc`

```jsonc
"name": "your-tracker",
...
"kv_namespaces": [{ "binding": "BETRAYAL_KV", "id": "YOUR-NEW-KV-ID" }],
```

Create a fresh KV namespace in Cloudflare for each tracker and paste its ID.

## 5. Re-theme the dashboard — `index.html`

One file holds all the branding. Change:

- `<title>` and the `.brand` header text (e.g. `BETRAYTRACKER`)
- the subheading line under the title
- the four stat-box labels and their icons (`ti-knife`, `ti-clock-hour-4`,
  `ti-users-group` — pick from https://tabler.io/icons)
- the **gauge** labels in `updateGauge()` (e.g. `Unbetrayed` →
  `HIGH LEVELS OF BETRAYAL`) and the chart titles in the `METRICS` object
- the colors in the `:root` CSS block (one place sets the whole palette)

## 6. (Optional) Pass keywords from the Action — `.github/workflows/scrape.yml`

If you'd rather not change the scraper default, pass them in the run step:

```yaml
run: python3 news_scraper.py --keywords scandal scandals scandalous
```

---

## Deploy (same as the main README)

1. Push the new repo to GitHub.
2. Cloudflare → **Workers & Pages** → create a **Worker** connected to the repo.
3. Create a **KV namespace**, put its ID in `wrangler.jsonc`.
4. Worker → **Settings → Build → Build watch paths**: exclude `data.json` so
   data commits don't trigger rebuilds.
5. Worker → **Settings → Domains & Routes**: add your domain.
6. **Actions** tab → run the scraper once to seed `data.json`.

That's the whole template. Everything topic-specific lives in the six spots
above; the plumbing (scrape → commit → mirror → serve) stays the same.
