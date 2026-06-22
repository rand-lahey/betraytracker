/*
 * BetrayTracker — Cloudflare Worker
 * =================================
 * Replaces the GitHub Action + committed data files. This Worker:
 *   • scheduled()  — runs on a cron (every 5 min), scrapes the data, stores it
 *                    in KV. No git commits, no Pages/Workers builds per update.
 *   • fetch()      — serves /data.json from KV (building it on demand if the KV
 *                    is still empty), and serves the dashboard static assets.
 *
 * Data sources (same as the Python scraper, ported to JS):
 *   • GDELT DOC 2.0 "timelinevolraw" — a full 24-hour volume curve in one call,
 *     so the chart always shows a complete 24h.
 *   • Google News RSS — the live "Recent betrayals" feed + head-count extraction.
 *
 * Bindings (see wrangler.jsonc):
 *   • ASSETS       — static assets (index.html)
 *   • BETRAYAL_KV  — KV namespace holding the latest computed state
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

/* ---- Google News RSS: live feed + head counts ---- */
async function fetchNews() {
  const q = orQuery() + " when:1d";
  const url = "https://news.google.com/rss/search?" +
    new URLSearchParams({ q, hl: "en-US", gl: "US", ceid: "US:en" });
  let xml;
  try {
    const r = await fetch(url, { headers: {
      "User-Agent": UA,
      "Accept": "application/rss+xml, application/xml, text/xml; q=0.9, */*; q=0.8",
      "Accept-Language": "en-US,en;q=0.9",
    } });
    if (!r.ok) return null;
    xml = await r.text();
  } catch (e) { return null; }
  const items = [...xml.matchAll(/<item>([\s\S]*?)<\/item>/g)].map((mm) => mm[1]);
  const recent = [];
  for (const it of items) {
    const grab = (re) => { const m = it.match(re); return m ? decodeXml(stripCdata(m[1])).trim() : ""; };
    let title = grab(/<title>([\s\S]*?)<\/title>/);
    const link = grab(/<link>([\s\S]*?)<\/link>/);
    const source = grab(/<source[^>]*>([\s\S]*?)<\/source>/);
    const pub = grab(/<pubDate>([\s\S]*?)<\/pubDate>/);
    if (source && title.endsWith(" - " + source)) title = title.slice(0, -(source.length + 3));
    recent.push({
      platform: "News", title: title.slice(0, 200), url: link,
      snippet: (source + (pub ? " · " + pub : "")).slice(0, 200),
      people: extractPeople(title),
    });
  }
  return { count: items.length, recent };
}

/* ---- GDELT: full 24-hour volume curve in one call ---- */
async function gdeltTimeline() {
  const q = orQuery() + " sourcelang:english";
  const url = "https://api.gdeltproject.org/api/v2/doc/doc?" +
    new URLSearchParams({ query: q, mode: "timelinevolraw", format: "json", timespan: "1d" });
  let data;
  try {
    const r = await fetch(url, { headers: {
      "User-Agent": UA,
      "Accept": "application/json, */*; q=0.8",
    } });
    if (!r.ok) return null;
    data = await r.json();
  } catch (e) { return null; }
  const series = (data.timeline || [])[0];
  if (!series || !series.data || !series.data.length) return null;
  const vals = series.data.map((p) => Number(p.value) || 0);
  return series.data.map((p, i) => ({
    t: parseGdeltDate(p.date),
    counts: { reddit: 0, twitter: 0, news: vals[i] },
    total: vals[i],
    per_hour: vals.slice(Math.max(0, i - 3), i + 1).reduce((a, b) => a + b, 0), // ~last hour
  }));
}

/* ---- assemble the full dashboard state ---- */
async function buildState() {
  const [news, timeline] = await Promise.all([fetchNews(), gdeltTimeline()]);
  const recent = news ? news.recent : [];
  const betrayed_total = recent.reduce((a, r) => a + (r.people || 0), 0);
  const articles_with_count = recent.filter((r) => r.people > 0).length;
  const now = new Date().toISOString();

  let history, total, per_hour, current_rate, source;
  if (timeline) {
    history = timeline;
    total = timeline.reduce((a, p) => a + p.total, 0);
    per_hour = Math.round((total / 24) * 10) / 10;
    current_rate = timeline[timeline.length - 1].per_hour;
    timeline[timeline.length - 1].betrayed_total = betrayed_total;
    timeline[timeline.length - 1].articles_with_count = articles_with_count;
    source = "GDELT 24h timeline + Google News";
  } else {
    total = news ? news.count : 0;
    per_hour = Math.round((total / 24) * 10) / 10;
    current_rate = per_hour;
    history = [{ t: now, counts: { reddit: 0, twitter: 0, news: total }, total,
      per_hour, betrayed_total, articles_with_count }];
    source = news ? "Google News" : "unavailable";
  }

  return {
    keyword: KEYWORDS.join(" / "),
    generated_at: now,
    history,
    latest: {
      t: now, counts: { reddit: 0, twitter: 0, news: total }, total,
      platform_labels: PLATFORM_LABELS, recent, source,
      per_hour, current_rate, betrayed_total, articles_with_count,
    },
  };
}

async function refresh(env) {
  const state = await buildState();
  await env.BETRAYAL_KV.put("state", JSON.stringify(state));
  return state;
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(refresh(env));
  },
  async fetch(request, env) {
    const url = new URL(request.url);

    // Diagnostic: shows the raw HTTP result of each upstream fetch.
    if (url.pathname === "/debug") {
      const out = {};
      try {
        const u = "https://news.google.com/rss/search?" + new URLSearchParams(
          { q: orQuery() + " when:1d", hl: "en-US", gl: "US", ceid: "US:en" });
        const r = await fetch(u, { headers: { "User-Agent": UA, "Accept": "application/rss+xml,*/*" } });
        const t = await r.text();
        out.googleNews = { status: r.status, ok: r.ok, bytes: t.length,
          items: (t.match(/<item>/g) || []).length, snippet: t.slice(0, 160) };
      } catch (e) { out.googleNews = { error: String(e) }; }
      try {
        const u = "https://api.gdeltproject.org/api/v2/doc/doc?" + new URLSearchParams(
          { query: orQuery() + " sourcelang:english", mode: "timelinevolraw", format: "json", timespan: "1d" });
        const r = await fetch(u, { headers: { "User-Agent": UA } });
        const t = await r.text();
        out.gdelt = { status: r.status, ok: r.ok, bytes: t.length, snippet: t.slice(0, 160) };
      } catch (e) { out.gdelt = { error: String(e) }; }
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
