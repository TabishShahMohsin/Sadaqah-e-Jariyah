// Cloudflare Pages Function: GET /api/visitors → {"count": n}. Each call counts one visit.
//
// The count lives in a D1 database (binding DB on the Pages project): one row, bumped atomically. D1's free plan
// allows 100,000 row writes a day; the KV namespace used before (binding VISITORS) allows only 1,000 writes a day,
// which the site reached in November 2026. On its first run with DB bound, the counter starts from KV's last count.
// Without DB it falls back to KV, and without either (local preview) to hits.sh, the original counter.
const START = 345;   // the count hits.sh showed when it stopped answering
const COUNTER = "https://hits.sh/sadaqah-e-jariyah.pages.dev.svg?style=flat-square&label=visitors&color=1d1d1f&labelColor=6e6e73";
const headers = { "Cache-Control": "no-store" };
const BUMP = "INSERT INTO counter (id, n) VALUES (1, ?1) ON CONFLICT(id) DO UPDATE SET n = n + 1 RETURNING n";

async function fromKv(env) {
  return parseInt(await env.VISITORS?.get("count"), 10) || START;
}

async function d1(env) {
  try {
    return (await env.DB.prepare(BUMP).bind(START + 1).first()).n;
  } catch (e) {
    if (!/no such table/i.test(String(e && e.message))) throw e;
    // first run: create the table and carry the count over from KV
    await env.DB.prepare("CREATE TABLE IF NOT EXISTS counter (id INTEGER PRIMARY KEY, n INTEGER NOT NULL)").run();
    return (await env.DB.prepare(BUMP).bind((await fromKv(env)) + 1).first()).n;
  }
}

export async function onRequestGet({ env, waitUntil }) {
  if (env.DB) {
    try {
      return Response.json({ count: await d1(env) }, { headers });
    } catch { /* fall through */ }
  }
  if (env.VISITORS) {
    try {
      const n = (await fromKv(env)) + 1;
      waitUntil(env.VISITORS.put("count", String(n)).catch(() => {}));   // fails quietly once KV's daily writes run out
      return Response.json({ count: n }, { headers });
    } catch { /* fall through to hits.sh */ }
  }
  try {
    const res = await fetch(COUNTER, { headers: { "User-Agent": "sadaqah-e-jariyah" } });
    const svg = res.ok ? await res.text() : "";
    const m = svg.match(/aria-label="[^"]*?:\s*([\d.,]+)\s*([kKmM]?)"/);
    if (!m) throw new Error("no count");
    const n = parseFloat(m[1].replace(/,/g, "")) * ({ k: 1e3, m: 1e6 }[m[2].toLowerCase()] || 1);
    return Response.json({ count: Math.round(n) }, { headers });
  } catch {
    return Response.json({ count: null }, { status: 502, headers });
  }
}
