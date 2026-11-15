// Cloudflare Pages Function in front of the static API files (api/v1/files.json, sources.json, programmes.json).
// It serves the file unchanged and counts the call in the D1 database (binding DB): one row per day, endpoint and
// client, so API use is counted apart from site visits (an app reading files.json is not a visitor).
// GET /api/v1/stats.json returns the last 30 days of those counts.
const HEAD = { "Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache" };
const TABLE = "CREATE TABLE IF NOT EXISTS api_calls (day TEXT NOT NULL, path TEXT NOT NULL, client TEXT NOT NULL, " +
              "n INTEGER NOT NULL, PRIMARY KEY (day, path, client))";
const BUMP = "INSERT INTO api_calls (day, path, client, n) VALUES (?1, ?2, ?3, 1) " +
             "ON CONFLICT(day, path, client) DO UPDATE SET n = n + 1";

// who is calling: the website's host for browser apps, else the program's name (python-requests, curl, …)
function client(req) {
  for (const h of ["Origin", "Referer"]) {
    try { const v = req.headers.get(h); if (v) return new URL(v).host.slice(0, 80); } catch { /* not a URL */ }
  }
  const ua = req.headers.get("User-Agent") || "";
  if (/Mozilla\//.test(ua)) return "browser";
  return (ua.split(/[\s/]/)[0] || "unknown").slice(0, 40);
}

async function run(db, sql, ...args) {
  try { return await db.prepare(sql).bind(...args).run(); }
  catch (e) {
    if (!/no such table/i.test(String(e && e.message))) throw e;
    await db.prepare(TABLE).run();
    return db.prepare(sql).bind(...args).run();
  }
}

async function stats(env) {
  if (!env.DB) return Response.json({ error: "not set up" }, { status: 503, headers: HEAD });
  const since = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);
  let rows = [];
  try {
    rows = (await env.DB.prepare("SELECT path, client, SUM(n) AS n FROM api_calls WHERE day >= ?1 GROUP BY path, client")
      .bind(since).all()).results || [];
  } catch { /* table not created yet: no calls */ }
  const by = (k) => Object.entries(rows.reduce((a, r) => (a[r[k]] = (a[r[k]] || 0) + r.n, a), {}))
    .sort((a, b) => b[1] - a[1]).map(([name, n]) => ({ [k]: name, calls: n }));
  return Response.json({ since, calls: rows.reduce((s, r) => s + r.n, 0), clients: new Set(rows.map(r => r.client)).size,
                         byEndpoint: by("path"), byClient: by("client").slice(0, 20) }, { headers: HEAD });
}

export async function onRequest({ request, env, next, waitUntil, params }) {
  const path = [].concat(params.path || []).join("/");
  if (request.method === "OPTIONS") return new Response(null, { headers: { ...HEAD, "Access-Control-Allow-Methods": "GET, HEAD" } });
  if (path === "stats.json") return stats(env);
  const res = await next();
  const out = new Response(res.body, res);
  for (const [k, v] of Object.entries(HEAD)) out.headers.set(k, v);
  if (env.DB && request.method === "GET" && res.ok) {
    const day = new Date().toISOString().slice(0, 10);
    waitUntil(run(env.DB, BUMP, day, path, client(request)).catch(() => {}));
  }
  return out;
}
