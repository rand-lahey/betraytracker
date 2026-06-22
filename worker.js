/*
 * BetrayTracker — Cloudflare Worker
 * =================================
 * Replaces the GitHub Action + committed data files. This Worker:
 *   • scheduled()  — runs on a cron (every 15 min), calls the GNews API, and
 *                    stores the computed state in KV. No git commits, no builds.
 *   • fetch()      — serves /data.json from KV (building it on demand if KV is
 *                    still empty), and serves the dashboard static assets.
 *
 * Data source: GNews API (https://gnews.io). Free news APIs like GDELT and
 * Google News block/throttle Cloudflare's shared egress IPs, so we use an
 * authenticated API instead, which works reliably from a Worker.
 *   • count = totalArticles matching in the last 24h  → "Betrayals (24h)"
 *   • the returned articles → "Recent betrayals" feed + head-count extraction
 *   • a 24h rolling history is accrued in KV (one point per run) for the chart
 *     and the Betrayometer.
 *
 * Bindings / secrets (see wrangler.jsonc):
 *   • ASSETS       — static assets (index.html)
 *   • BETRAYAL_KV  — KV namespace holding the latest computed state + history
 *   • GNEWS_KEY    — GNews API key (set as an encrypted Secret in the dashboard)
 */

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";
const KEYWORDS = ["betrayal", "betray", "betrayed", "betrayer", "betraying"];
const PLATFORM_LABELS = { reddit: "Reddit", twitter: "X / Twitter", news: "News" };

/* ---- head-count heuristic (ported from news_scraper.py) ---- */
const NOUNS = "people|persons?|victims?|employees?|workers?|staff|members?|fans?|" +
  "customers?|users?|soldiers?|troops|residents?|families|women|men|children|kids|" +
  "students?|players?|voters?|citizens?|patients?|investors?|depositors?|passengers?|" +
  "survivors?|refugees?|migrants?|hostages?|prisoners?|inmates?|colleagues?|friends?|" +
  "partners?|teammates?|allies|supporters?|followers?|shareholders?|nurses?|doctors?|" +
  "officers?|veterans?|seniors?|tenants?|homeowners?";
const WORD_NUM = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7,
  eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, dozen: 12, hundred: 100,
  thousand: 1000 };
const SCALE = { hundred: 100, thousand: 1000, million: 1e6, billion: 1e9 };

function applyScale(num, gap) {
  for (const w in SCALE) if (new RegExp("\\b" + w + "\\b").test(gap)) return num * SCALE[w];
  return num;
}
function extractPeople(text) {
  const t = (text || "").toLowerCase();
  let best = 0, m;
  const digitRe = new RegExp("(\\d[\\d,]*)\\s+((?:\\w+\\s+){0,2}?)(" + NOUNS + ")\\b", "g");
  while ((m = digitRe.exec(t))) {
    const n = parseInt(m[1].replace(/,/g, ""), 10);
    if (!isNaN(n)) best = Math.max(best, applyScale(n, m[2]));
  }
  const wordRe = new RegExp("\\b(" + Object.keys(WORD_NUM).join("|") +
    ")\\s+((?:\\w+\\s+){0,2}?)(" + NOUNS + ")\\b", "g");
  while ((m = wordRe.exec(t))) best = Math.max(best, applyScale(WORD_NUM[m[1]], m[2]));
  return best;
}

/* ---- small XML helpers ---- */
function stripCdata(s) { const m = s.match(/<!\[CDATA\[([\s\S]*?)\]\]>/); return m ? m[1] : s; }
function decodeXml(s) {
  return s
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCharCode(parseInt(n, 16)))
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&");
}
function parseGdeltDate(s) {
  const m = s.match(/(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/);
  return m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z` : s;
}
function orQuery() { return KEYWORDS.length > 1 ? "(" + KEYWORDS.join(" OR ") + ")" : KEYWORDS[0]; }

/* ---- GNews API: news that allows authenticated server access ---- */
const GNEWS = "https://gnews.io/api/v4/search";

async function fetchGNews(env) {
  const key = env.GNEWS_KEY;
  if (!key) return null;
  const from = new Date(Date.now() - 24 * 3600 * 1000).toISOString(); // last 24h
  const url = GNEWS + "?" + new URLSearchParams({
    q: orQuery(), lang: "en", max: "10", sortby: "publishedAt",
    in: "title,description", from, apikey: key,
  });
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    const data = await r.json();
    const arts = data.articles || [];
    const recent = arts.map((a) => ({
      platform: "News",
      title: (a.title || "").slice(0, 200),
      url: a.url || "",
      snippet: (((a.source && a.source.name) || "") +
        (a.publishedAt ? " · " + a.publishedAt : "")).slice(0, 200),
      people: extractPeople((a.title || "") + " " + (a.description || "")),
    }));
    return { total: data.totalArticles ?? arts.length, recent };
  } catch (e) { return null; }
}

/* ---- refresh: call GNews, accrue a 24h rolling history in KV ---- */
async function refresh(env) {
  const prev = JSON.parse((await env.BETRAYAL_KV.get("state")) || "null") || {};
  const now = new Date().toISOString();
  const gn = await fetchGNews(env);

  if (!gn) {
    // Keep the last good data rather than wiping to zeros (e.g. quota/transient).
    if (prev.latest) return prev;
    const empty = {
      keyword: KEYWORDS.join(" / "), generated_at: now, history: [],
      latest: { t: now, counts: { reddit: 0, twitter: 0, news: 0 }, total: 0,
        platform_labels: PLATFORM_LABELS, recent: [],
        source: env.GNEWS_KEY ? "unavailable" : "no API key",
        per_hour: 0, current_rate: 0, betrayed_total: 0, articles_with_count: 0 },
    };
    return empty; // not stored, so the next run retries
  }

  const total = gn.total;
  const recent = gn.recent;
  const betrayed_total = recent.reduce((a, r) => a + (r.people || 0), 0);
  const articles_with_count = recent.filter((r) => r.people > 0).length;
  const per_hour = Math.round((total / 24) * 10) / 10;

  // Append a reading and keep only the last 24 hours of points.
  const point = { t: now, counts: { reddit: 0, twitter: 0, news: total }, total,
    per_hour, betrayed_total, articles_with_count };
  let history = Array.isArray(prev.history) ? prev.history : [];
  history.push(point);
  const cutoff = Date.now() - 24 * 3600 * 1000;
  history = history.filter((p) => new Date(p.t).getTime() >= cutoff).slice(-300);

  const state = {
    keyword: KEYWORDS.join(" / "), generated_at: now, history,
    latest: { t: now, counts: { reddit: 0, twitter: 0, news: total }, total,
      platform_labels: PLATFORM_LABELS, recent, source: "GNews",
      per_hour, current_rate: per_hour, betrayed_total, articles_with_count },
  };
  await env.BETRAYAL_KV.put("state", JSON.stringify(state));
  return state;
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(refresh(env));
  },
  async fetch(request, env) {
    const url = new URL(request.url);

    // Diagnostic: tests the GNews call and shows status + counts (no key leaked).
    if (url.pathname === "/debug") {
      const out = { hasKey: !!env.GNEWS_KEY };
      try {
        const from = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
        const u = GNEWS + "?" + new URLSearchParams({
          q: orQuery(), lang: "en", max: "10", sortby: "publishedAt",
          in: "title,description", from, apikey: env.GNEWS_KEY || "" });
        const r = await fetch(u);
        const t = await r.text();
        let parsed = null; try { parsed = JSON.parse(t); } catch (e) {}
        out.gnews = { status: r.status, ok: r.ok,
          totalArticles: parsed ? parsed.totalArticles : undefined,
          articles: parsed && parsed.articles ? parsed.articles.length : undefined,
          snippet: t.slice(0, 160) };
      } catch (e) { out.gnews = { error: String(e) }; }
      return new Response(JSON.stringify(out, null, 2),
        { headers: { "content-type": "application/json" } });
    }

    // Force a rebuild now and report the result.
    if (url.pathname === "/refresh") {
      const st = await refresh(env);
      return new Response(JSON.stringify(
        { ok: true, total: st.latest.total, source: st.latest.source,
          points: st.history.length }, null, 2),
        { headers: { "content-type": "application/json" } });
    }

    if (url.pathname === "/data.json") {
      let s = await env.BETRAYAL_KV.get("state");
      if (!s) s = JSON.stringify(await refresh(env)); // cold start: build on demand
      return new Response(s, {
        headers: {
          "content-type": "application/json; charset=utf-8",
          "cache-control": "no-store",
          "access-control-allow-origin": "*",
        },
      });
    }
    return env.ASSETS.fetch(request);
  },
};
