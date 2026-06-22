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

/* ---- GDELT helper (respects "1 request / 5s" with spacing + retry) ---- */
const GDELT = "https://api.gdeltproject.org/api/v2/doc/doc";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function gdeltGet(params, retries = 2) {
  const url = GDELT + "?" + new URLSearchParams(params);
  for (let i = 0; i < retries; i++) {
    try {
      const r = await fetch(url, { headers: { "User-Agent": UA } });
      if (r.status === 429) { if (i < retries - 1) { await sleep(6000); continue; } return null; }
      if (!r.ok) return null;
      return await r.text();
    } catch (e) {
      if (i < retries - 1) { await sleep(3000); continue; }
      return null;
    }
  }
  return null;
}

/* ---- GDELT: full 24-hour volume curve (15-min buckets) ---- */
async function gdeltTimeline() {
  const txt = await gdeltGet({
    query: orQuery() + " sourcelang:english",
    mode: "timelinevolraw", format: "json", timespan: "1d",
  });
  if (!txt) return null;
  let data; try { data = JSON.parse(txt); } catch (e) { return null; }
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

/* ---- GDELT: recent articles for the feed + head-count ---- */
async function gdeltArtlist() {
  const txt = await gdeltGet({
    query: orQuery() + " sourcelang:english",
    mode: "artlist", maxrecords: 60, format: "json", timespan: "1d", sort: "datedesc",
  });
  if (!txt) return null;
  let data; try { data = JSON.parse(txt); } catch (e) { return null; }
  if (!("articles" in data)) return null;
  return (data.articles || []).slice(0, 60).map((a) => ({
    platform: "News",
    title: (a.title || "").trim().slice(0, 200),
    url: a.url || "",
    snippet: ((a.domain || "") + (a.seendate ? " · " + a.seendate : "")).slice(0, 200),
    people: extractPeople(a.title),
  }));
}

/* ---- assemble the full dashboard state (GDELT only) ---- */
async function buildState() {
  const timeline = await gdeltTimeline();
  await sleep(6000); // honour GDELT's "one request every 5 seconds"
  const recent = (await gdeltArtlist()) || [];
  const betrayed_total = recent.reduce((a, r) => a + (r.people || 0), 0);
  const articles_with_count = recent.filter((r) => r.people > 0).length;
  const now = new Date().toISOString();

  let history, total, per_hour, current_rate, source;
  if (timeline) {
    history = timeline;
    total = timeline.reduce((a, p) => a + p.total, 0);
    per_hour = Math.round((total / 24) * 10) / 10;
    current_rate = timeline[timeline.length - 1].per_hour;
    if (recent.length) {
      timeline[timeline.length - 1].betrayed_total = betrayed_total;
      timeline[timeline.length - 1].articles_with_count = articles_with_count;
    }
    source = "GDELT";
  } else if (recent.length) {
    total = recent.length;
    per_hour = Math.round((total / 24) * 10) / 10;
    current_rate = per_hour;
    history = [{ t: now, counts: { reddit: 0, twitter: 0, news: total }, total,
      per_hour, betrayed_total, articles_with_count }];
    source = "GDELT (articles only)";
  } else {
    total = 0; per_hour = 0; current_rate = 0;
    history = [{ t: now, counts: { reddit: 0, twitter: 0, news: 0 }, total: 0,
      per_hour: 0, betrayed_total: 0, articles_with_count: 0 }];
    source = "unavailable";
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

    // Diagnostic: tests the two GDELT calls, spaced out so it doesn't
    // trip GDELT's own "1 request / 5s" limit. (Don't spam this endpoint.)
    if (url.pathname === "/debug") {
      const out = {};
      const probe = async (params) => {
        try {
          const u = GDELT + "?" + new URLSearchParams(params);
          const r = await fetch(u, { headers: { "User-Agent": UA } });
          const t = await r.text();
          return { status: r.status, ok: r.ok, bytes: t.length, snippet: t.slice(0, 140) };
        } catch (e) { return { error: String(e) }; }
      };
      out.timeline = await probe({ query: orQuery() + " sourcelang:english",
        mode: "timelinevolraw", format: "json", timespan: "1d" });
      await sleep(6000);
      out.artlist = await probe({ query: orQuery() + " sourcelang:english",
        mode: "artlist", maxrecords: 5, format: "json", timespan: "1d", sort: "datedesc" });
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
