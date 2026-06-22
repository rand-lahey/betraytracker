/*
 * BetrayTracker — Cloudflare Worker (data mirror + server)
 * ========================================================
 * The scraping now happens in GitHub Actions (news_scraper.py), because the
 * free news sources (Google News, GDELT) block/throttle Cloudflare's shared
 * egress IPs but work fine from GitHub's runners. The Action commits the
 * computed data.json to the repo; this Worker just mirrors that file and serves
 * it, so the dashboard's `/data.json` is fast and always available.
 *
 *   • scheduled()  — every 10 min, fetch the latest data.json from GitHub raw
 *                    and cache it in KV.
 *   • fetch()      — serve /data.json from KV (fetching once on cold start),
 *                    and serve the dashboard static assets (index.html).
 *
 * Bindings (see wrangler.jsonc):
 *   • ASSETS       — static assets (index.html)
 *   • BETRAYAL_KV  — KV namespace caching the latest data.json
 */

const DATA_URL =
  "https://raw.githubusercontent.com/rand-lahey/betraytracker/main/data.json";

async function fetchData() {
  try {
    const r = await fetch(DATA_URL, {
      cf: { cacheTtl: 60, cacheEverything: true },
      headers: { "User-Agent": "BetrayTracker-Worker" },
    });
    if (!r.ok) return null;
    const txt = await r.text();
    JSON.parse(txt); // sanity check it's valid JSON
    return txt;
  } catch (e) {
    return null;
  }
}

async function refresh(env) {
  const txt = await fetchData();
  if (txt) await env.BETRAYAL_KV.put("state", txt);
  return txt;
}

const EMPTY = JSON.stringify({
  keyword: "betrayal / betray / betrayed / betrayer / betraying",
  history: [],
  latest: {
    t: new Date().toISOString(),
    counts: { reddit: 0, twitter: 0, news: 0 }, total: 0,
    platform_labels: { reddit: "Reddit", twitter: "X / Twitter", news: "News" },
    recent: [], source: "waiting for first scrape",
    per_hour: 0, current_rate: 0, betrayed_total: 0, articles_with_count: 0,
  },
});

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(refresh(env));
  },
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/data.json") {
      let s = await env.BETRAYAL_KV.get("state");
      if (!s) s = await refresh(env);   // cold start
      return new Response(s || EMPTY, {
        headers: {
          "content-type": "application/json; charset=utf-8",
          "cache-control": "no-store",
          "access-control-allow-origin": "*",
        },
      });
    }

    if (url.pathname === "/refresh") {
      const s = await refresh(env);
      return new Response(
        JSON.stringify({ ok: !!s, bytes: s ? s.length : 0, source: DATA_URL }),
        { headers: { "content-type": "application/json" } });
    }

    return env.ASSETS.fetch(request);
  },
};
