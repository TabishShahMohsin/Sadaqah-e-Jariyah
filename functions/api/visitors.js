// Cloudflare Pages Function: GET /api/visitors → {"count": n}
// Each call counts one visit on hits.sh; the number is read from its badge so the site can show it as plain text.
// Served from our own domain, so ad blockers don't stop it.
const COUNTER = "https://hits.sh/sadaqah-e-jariyah.pages.dev.svg?style=flat-square&label=visitors&color=1d1d1f&labelColor=6e6e73";
const headers = { "Cache-Control": "no-store" };

export async function onRequestGet() {
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
