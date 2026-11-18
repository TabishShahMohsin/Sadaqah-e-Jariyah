// Cloudflare Pages Function in front of every page: the site's address is sej.org.in.
// A browser or search engine that opens a page on sadaqah-e-jariyah.pages.dev is sent there with a permanent
// redirect (same path and query). Everything else on pages.dev keeps working, because sej.org.in itself loads its
// code, data and API from pages.dev (served from India; see FAST in site/app.js): only page requests are redirected.
// Preview deployments (<id>.sadaqah-e-jariyah.pages.dev) are left alone, so a deploy can be checked before it is live.
// _routes.json (written by tools/hf_publish.py) keeps the static assets from running this at all.
const HOME = "https://sej.org.in";

export async function onRequest({ request, next }) {
  const url = new URL(request.url);
  const page = (request.method === "GET" || request.method === "HEAD") && !url.pathname.startsWith("/api/") &&
               (request.headers.get("Accept") || "").includes("text/html");
  if (url.hostname === "sadaqah-e-jariyah.pages.dev" && page) {
    return Response.redirect(HOME + url.pathname + url.search, 301);
  }
  return next();
}
