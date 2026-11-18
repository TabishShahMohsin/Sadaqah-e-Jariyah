// Cloudflare Pages Function: course views, for "Hot right now" (shown on the staging site, see site/app.js).
//
//   POST /api/views   body {"p": "<course path>"}  → counts one view of that course today (the site sends it once per
//                     course per browser session, with navigator.sendBeacon)
//   GET  /api/views   → {"days": 7, "since": "YYYY-MM-DD", "courses": [{"p": "<course path>", "n": views}, …]}
//                     the most viewed courses of the last 7 days, newest data cached for 5 minutes
//
// Stored in the D1 database (binding DB) as one row per day and course: a few hundred small rows a day at most.
// Only paths that look like the archive's course folders are counted, and only from the site's own pages.
const HEAD = { "Access-Control-Allow-Origin": "*" };
const TABLE = "CREATE TABLE IF NOT EXISTS course_views (day TEXT NOT NULL, course TEXT NOT NULL, n INTEGER NOT NULL, " +
              "PRIMARY KEY (day, course))";
const BUMP = "INSERT INTO course_views (day, course, n) VALUES (?1, ?2, 1) ON CONFLICT(day, course) DO UPDATE SET n = n + 1";
const TOP = "SELECT course AS p, SUM(n) AS n FROM course_views WHERE day >= ?1 GROUP BY course ORDER BY n DESC LIMIT 400";
const DAYS = 7;
const OURS = /^https:\/\/((www\.)?sej\.org\.in|([a-z0-9-]+\.)?sadaqah-e-jariyah\.pages\.dev)(\/|$)/;
const COURSE = /^Faculty-of-[A-Za-z-]+\/[A-Za-z0-9&()._\/-]{3,300}$/;

async function run(db, sql, ...args) {
  try { return await db.prepare(sql).bind(...args).run(); }
  catch (e) {
    if (!/no such table/i.test(String(e && e.message))) throw e;
    await db.prepare(TABLE).run();
    return db.prepare(sql).bind(...args).run();
  }
}

export async function onRequestPost({ request, env, waitUntil }) {
  const from = request.headers.get("Origin") || request.headers.get("Referer") || "";
  if (!env.DB || !OURS.test(from)) return new Response(null, { status: 204, headers: HEAD });
  let p = "";
  try { p = String(JSON.parse(await request.text()).p || ""); } catch { /* not ours */ }
  if (COURSE.test(p) && !p.includes("..")) {
    waitUntil(run(env.DB, BUMP, new Date().toISOString().slice(0, 10), p).catch(() => {}));
  }
  return new Response(null, { status: 204, headers: HEAD });
}

export async function onRequestGet({ env }) {
  const since = new Date(Date.now() - (DAYS - 1) * 864e5).toISOString().slice(0, 10);
  let courses = [];
  if (env.DB) {
    try { courses = (await env.DB.prepare(TOP).bind(since).all()).results || []; }
    catch { /* table not created yet: no views */ }
  }
  return Response.json({ days: DAYS, since, courses },
    { headers: { ...HEAD, "Cache-Control": "public, max-age=300" } });
}

export function onRequestOptions() {
  return new Response(null, { headers: { ...HEAD, "Access-Control-Allow-Methods": "GET, POST", "Access-Control-Allow-Headers": "Content-Type" } });
}
