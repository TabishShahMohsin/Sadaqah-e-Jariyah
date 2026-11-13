// Cloudflare Pages Function: GET /api/visitors → {"count": n}. Each call counts one visit.
// The count lives in our own KV namespace (binding VISITORS on the Pages project), so it doesn't depend on a third
// party. hits.sh, the counter used until November 2026, started failing; its last count is the starting point.
// Without the binding (local preview, or before it is set up) it still tries hits.sh.
const START = 345;   // the count hits.sh showed when it stopped answering
const COUNTER = "https://hits.sh/sadaqah-e-jariyah.pages.dev.svg?style=flat-square&label=visitors&color=1d1d1f&labelColor=6e6e73";
const headers = { "Cache-Control": "no-store" };

export async function onRequestGet({ env, waitUntil }) {
  if (env.VISITORS) {
    try {
      const n = (parseInt(await env.VISITORS.get("count"), 10) || START) + 1;
      waitUntil(env.VISITORS.put("count", String(n)).catch(() => {}));   // a lost write under load only loses a visit
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
