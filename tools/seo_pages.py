"""
Static pages for search engines.

The site is one app (site/app.js) that draws every page from index.json. Search engines list addresses, and they
read a page best when its text is in the HTML they download, so every folder down to course level also gets a
real page: _dist/<path>/index.html, with its own title, description and canonical address and its contents
(sub-folders, or the course's files by section) written out. The app starts on that page and takes over as usual.

Also written: sitemap.xml and robots.txt. Called by `hf_publish.py site`.
"""
import html
import json
import os
import re
import shutil
import urllib.parse
from datetime import datetime, timezone

SITE_URL = "https://sej.org.in"
MANIFEST = ".seo-pages.json"  # the page folders written last time, so pages of moved folders don't linger
SECTIONS = ["Syllabus", "Complete-Notes", "Unit-1", "Unit-2", "Unit-3", "Unit-4", "Unit-5", "PYQs-and-Assignments",
            "Manuals", "Records", "Books-and-Resources"]
COURSE_RE = re.compile(r"^((?:[A-Z]{2,4}\d[0-9A-Z]\d{1,2}[A-Z]?|(?:DE|OE)-\d+)(?:-[A-Z]{2,4}\d[0-9A-Z]\d{1,2}[A-Z]?)*)_(.+)$")
PROF = {"1": "1st", "2": "2nd", "3": "3rd", "4": "4th"}
SPECIAL = {"contribute": ("Contribute notes and papers", "Share your notes, previous year papers or lab work with AMU students: drop the files and they are sorted into the right course."),
           "credits": ("Sources & credits", "Where every file in the archive came from, and the students and teachers who shared them."),
           "data": ("Download & API", "Download the whole archive or use its open JSON API.")}
esc = html.escape


def names_from_app(app_js):
    """The display names in site/app.js (const NAMES = {...}), so both read the same."""
    m = re.search(r"const NAMES = \{(.*?)\n\};", app_js, re.S)
    return dict(re.findall(r"'([^']+)':\s*'([^']*)'", m.group(1))) if m else {}


def url_of(path, folder=True):
    if not path:
        return "/"
    enc = "/".join(urllib.parse.quote(s, safe="!'()*-._~") for s in path.split("/"))
    return "/" + enc + ("/" if folder else "")


class Pages:
    def __init__(self, idx, app_js):
        pg = idx.get("programmes") or {}
        self.names = {**names_from_app(app_js), **(pg.get("labels") or {}), "BTech-and-BE": "B.Tech & B.E.",
                      **(pg.get("umbrellas") or {})}
        self.courses = pg.get("courses") or {}

    def label(self, n):
        if n in self.names:
            return self.names[n]
        m = re.fullmatch(r"Sem(\d+)", n)
        if m:
            return "Semester " + m.group(1)
        m = re.fullmatch(r"Prof-(\d)", n)
        if m:
            return PROF[m.group(1)] + " Professional"
        m = re.fullmatch(r"Unit-(\d+)", n)
        if m:
            return "Unit " + m.group(1)
        m = COURSE_RE.match(n)
        if m:
            return m.group(2).replace("-", " ")
        return re.sub(r"([A-Za-z])-(?=[A-Za-z])", r"\1 ", n.lstrip("_").replace("_", " · "))

    @staticmethod
    def is_course(d, parent):
        return parent is not None and (bool(COURSE_RE.match(d["n"])) or any(x["n"] in SECTIONS for x in d["d"]))

    @staticmethod
    def count(d):
        return len(d["f"]) + sum(Pages.count(x) for x in d["d"])

    @staticmethod
    def latest(d):
        return max([f[2] for f in d["f"]] + [Pages.latest(x) for x in d["d"]] + [0])

    def file_name(self, f):
        meta = f[3] if len(f) > 3 and f[3] else {}
        if meta.get("title"):
            return meta["title"]
        stem = f[0].rsplit(".", 1)[0] if "." in f[0] else f[0]
        return re.sub(r"([A-Za-z])-(?=[A-Za-z])", r"\1 ", re.sub(r"^Z_", "", stem).replace("_", " · "))

    def file_links(self, d, path):
        out = []
        for f in sorted(d["f"], key=lambda f: (f[0].startswith("Z_"), f[0].lower())):
            meta = f[3] if len(f) > 3 and f[3] else {}
            fp = f"{path}/{f[0]}"
            if f[0].endswith(".link") and str(meta.get("url", "")).startswith("https://"):
                out.append(f'<li><a href="{esc(meta["url"])}" rel="nofollow noopener" target="_blank">{esc(self.file_name(f))}</a></li>')
            else:
                out.append(f'<li><a href="{esc(url_of(fp, False))}">{esc(self.file_name(f))}</a></li>')
        return out

    # ── one page ────────────────────────────────────────────────────────────
    def page(self, d, path, chain, course):
        """chain: [(name, path)] of the ancestors (without this folder). → (title, description, body html)"""
        lab = self.label(d["n"])
        n = self.count(d)
        where = [self.label(a) for a, _ in chain]
        ctx = ", ".join(reversed(where))
        if course:
            m = COURSE_RE.match(d["n"])
            meta = self.courses.get(path, {})
            codes = (meta.get("codes") or (m.group(1).split("-") if m else []))
            code_s = " / ".join(codes)
            prog = where[1] if len(where) > 1 else ""
            title = f"{lab}" + (f" ({code_s})" if code_s else "") + f" – Notes, PYQs & Syllabus | {where[-2] if len(where) > 1 else 'AMU'}, AMU"
            desc = (f"Free {lab}" + (f" ({code_s})" if code_s else "") + f" notes, previous year question papers (PYQs), syllabus "
                    f"and study material for {ctx} at Aligarh Muslim University. {n} files, shared by AMU students and teachers.")
            body = [f'<header class="page-head"><div class="eyebrow">{esc(" · ".join(where[-2:]))}</div>',
                    f"<h1>{esc(lab)}</h1>", f'<p class="lede-sm">{esc(desc)}</p></header>']
            if d["f"]:
                body += ['<h2 class="label">Files</h2><ul class="seo-list">', *self.file_links(d, path), "</ul>"]
            order = {s: i for i, s in enumerate(SECTIONS)}
            for s in sorted(d["d"], key=lambda x: (order.get(x["n"], 99), x["n"])):
                sp = f"{path}/{s['n']}"
                links = self.file_links(s, sp)
                for sub in s["d"]:  # e.g. Unit-2/Video-Lectures
                    links += self.file_links(sub, f"{sp}/{sub['n']}")
                if links:
                    body += [f'<h2 class="label">{esc(self.label(s["n"]))} <b>{self.count(s)}</b></h2><ul class="seo-list">', *links, "</ul>"]
            if prog:
                body.append(f'<p class="lede-sm">Part of <a href="{esc(url_of(chain[-1][1]))}">{esc(where[-1])}</a>, {esc(prog)} at Aligarh Muslim University.</p>')
        else:
            kids = [x for x in d["d"]]
            title = f"{lab}" + (f" – {where[-1]}" if where else "") + " | AMU Notes, PYQs & Syllabus"
            desc = (f"{lab}" + (f" ({ctx})" if ctx else "") + ": free notes, previous year question papers (PYQs), syllabi and lab work "
                    f"at Aligarh Muslim University (AMU). {n} files, shared by students and teachers.")
            body = [f'<header class="page-head"><div class="eyebrow">{esc(where[-1] if where else "Aligarh Muslim University")}</div>',
                    f"<h1>{esc(lab)}</h1>", f'<p class="lede-sm">{esc(desc)}</p></header>']
            if kids:
                body.append('<ul class="seo-list">')
                for k in sorted(kids, key=lambda x: self.label(x["n"]).lower()):
                    body.append(f'<li><a href="{esc(url_of(path + "/" + k["n"]))}">{esc(self.label(k["n"]))}</a> · {self.count(k)} files</li>')
                body.append("</ul>")
            if d["f"]:
                body += ['<h2 class="label">Files</h2><ul class="seo-list">', *self.file_links(d, path), "</ul>"]
        return title, desc, "\n".join(body)


def fill(template, url, title, desc, body, crumbs, ld):
    h = template
    h = re.sub(r"<title>.*?</title>", f"<title>{esc(title)}</title>", h, count=1, flags=re.S)
    h = re.sub(r'<meta name="description" content="[^"]*">', f'<meta name="description" content="{esc(desc)}">', h, count=1)
    h = re.sub(r'<link rel="canonical" href="[^"]*">', f'<link rel="canonical" href="{esc(url)}">', h, count=1)
    h = re.sub(r'<meta property="og:title" content="[^"]*">', f'<meta property="og:title" content="{esc(title)}">', h, count=1)
    h = re.sub(r'<meta property="og:description" content="[^"]*">', f'<meta property="og:description" content="{esc(desc)}">', h, count=1)
    h = re.sub(r'<meta property="og:url" content="[^"]*">', f'<meta property="og:url" content="{esc(url)}">', h, count=1)
    if ld:
        h = h.replace("</head>", '<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False).replace("</", "<\\/") + "</script>\n</head>", 1)
    h = h.replace('<nav id="crumbs" aria-label="Breadcrumb"></nav>', f'<nav id="crumbs" aria-label="Breadcrumb">{crumbs}</nav>', 1)
    h = re.sub(r'<main id="view">.*?</main>', lambda _: f'<main id="view">\n{body}\n</main>', h, count=1, flags=re.S)
    return h


def write(out, idx, template, app_js):
    """Write the pages, sitemap.xml and robots.txt into out/. → number of pages."""
    P = Pages(idx, app_js)
    man = os.path.join(out, MANIFEST)
    try:
        old = json.load(open(man, encoding="utf-8"))
    except (OSError, ValueError):
        old = []
    for rel in old:  # pages written last time (generated output, rebuilt below)
        f = os.path.join(out, rel, "index.html")
        if os.path.isfile(f):
            os.remove(f)
    for rel in sorted(old, key=lambda r: -r.count("/")):
        try:
            os.rmdir(os.path.join(out, rel))
        except OSError:
            pass
    written, urls = [], [(SITE_URL + "/", P.latest(idx["root"]))]

    def emit(rel, url, title, desc, body, crumbs, ld):
        d = os.path.join(out, *rel.split("/"))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(fill(template, url, title, desc, body, crumbs, ld))
        written.append(rel)

    def walk(d, path, chain):
        for c in d["d"]:
            p = f"{path}/{c['n']}" if path else c["n"]
            course = P.is_course(c, d)
            title, desc, body = P.page(c, p, chain, course)
            items = [("Home", "/")] + [(P.label(a), url_of(ap)) for a, ap in chain] + [(P.label(c["n"]), url_of(p))]
            ld = {"@context": "https://schema.org", "@type": "BreadcrumbList",
                  "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": nm, "item": SITE_URL + u} for i, (nm, u) in enumerate(items)]}
            crumbs = "".join(f'<span class="sep">›</span><a href="{esc(u)}">{esc(nm)}</a>' for nm, u in items[1:])
            url = SITE_URL + url_of(p)
            emit(p, url, title, desc, body, crumbs, ld)
            urls.append((url, P.latest(c)))
            if not course:
                walk(c, p, chain + [(c["n"], p)])

    walk(idx["root"], "", [])
    for key, (title, desc) in SPECIAL.items():
        emit(key, f"{SITE_URL}/{key}/", f"{title} | Sadaqah-e-Jariyah", desc,
             f'<header class="page-head"><h1>{esc(title)}</h1><p class="lede-sm">{esc(desc)}</p></header>',
             f'<span class="sep">›</span><a href="/{key}/">{esc(title)}</a>', None)
        urls.append((f"{SITE_URL}/{key}/", 0))
    with open(man, "w", encoding="utf-8") as fh:
        json.dump(written, fh, ensure_ascii=False)

    def day(t):
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d") if t else None
    with open(os.path.join(out, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for u, t in urls:
            fh.write(f"  <url><loc>{esc(u)}</loc>" + (f"<lastmod>{day(t)}</lastmod>" if t else "") + "</url>\n")
        fh.write("</urlset>\n")
    with open(os.path.join(out, "robots.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n")
    return len(written)
