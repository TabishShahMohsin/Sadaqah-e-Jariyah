/* Sadaqah-e-Jariyah — static browser over index.json. No build step, no framework. */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

const S = { pg: null, root: null, base: 'files/', generated: null, nodes: new Map(), files: new Map(), all: [], courses: [], rendered: null, preview: null };

/* ── naming ─────────────────────────────────────────── */
const NAMES = {
  'First-Year-Common': 'First Year (Common)',
  'Computer-Engineering': 'Computer Engineering & Artificial Intelligence',
  'Phys-Sem': 'Physics Semester',
  'Chem-Sem': 'Chemistry Semester',
  'PYQs-and-Assignments': 'PYQs & Assignments',
  'Books-and-Resources': 'Books & Resources',
  'Complete-Notes': 'Complete Notes',
  'Video-Lectures': 'Video Lectures',
  '_General-PYQs': 'General PYQs',
  '_Old-Syllabus-Subjects': 'Old-Syllabus Subjects',
  '_Curriculum': 'Curriculum & Notices',
  '_Library-Papers-Unsorted': 'Library papers, not yet sorted',
  'Official': 'Official Curriculum & Syllabus',
};
const SECTION_ABBR = { 'Syllabus': 'S', 'Complete-Notes': 'N', 'PYQs-and-Assignments': 'Q', 'Books-and-Resources': 'B', 'Manuals': 'M', 'Records': 'R' };
const isSection = n => n in SECTION_ABBR || /^Unit-\d+$/.test(n);
function sectionRank(n) {
  const u = n.match(/^Unit-(\d+)$/);
  if (u) return 10 + +u[1];
  return { 'Syllabus': 0, 'Complete-Notes': 1, 'PYQs-and-Assignments': 50, 'Manuals': 60, 'Records': 61, 'Books-and-Resources': 70 }[n] ?? 80;
}
// sej.org.in reaches Indian networks through Cloudflare data centres in Europe (seconds per request), while
// sadaqah-e-jariyah.pages.dev is served from Delhi. On the custom domain, data and code come from pages.dev.
const FAST = /(^|\.)sej\.org\.in$/.test(location.hostname) ? 'https://sadaqah-e-jariyah.pages.dev/' : '';
const COURSE_RE = /^((?:[A-Z]{2,4}\d{3,4}[A-Z]?|(?:DE|OE)-\d+)(?:-[A-Z]{2,4}\d{3,4}[A-Z]?)*)_(.+)$/;
function parseCourse(n) {
  const m = n.match(COURSE_RE);
  if (!m) return null;
  const codes = /^(DE|OE)-/.test(m[1]) ? [m[1]] : m[1].split('-');
  return { codes, title: m[2].replace(/-/g, ' ') };
}
function label(n) {
  if (NAMES[n]) return NAMES[n];
  let m;
  if ((m = n.match(/^Sem(\d+)$/))) return 'Semester ' + m[1];
  if ((m = n.match(/^Unit-(\d+)$/))) return 'Unit ' + m[1];
  const c = parseCourse(n);
  if (c) return c.title;
  return n.replace(/^_+/, '').replace(/_/g, ' · ').replace(/([A-Za-z])-(?=[A-Za-z])/g, '$1 ');
}
const ext = n => { const i = n.lastIndexOf('.'); return i > 0 ? n.slice(i + 1).toLowerCase() : ''; };
const stem = n => { const i = n.lastIndexOf('.'); return i > 0 ? n.slice(0, i) : n; };
const prettyFile = n => stem(n).replace(/^Z_/, '').replace(/_/g, ' · ').replace(/([A-Za-z])-(?=[A-Za-z])/g, '$1 ');

const KIND = { pdf: 'pdf', png: 'img', jpg: 'img', jpeg: 'img', gif: 'img', webp: 'img', svg: 'img', bmp: 'img',
  mp4: 'video', webm: 'video', mov: 'video', m4v: 'video', mkv: 'video', mp3: 'audio', wav: 'audio', m4a: 'audio', ogg: 'audio',
  link: 'link', ipynb: 'nb', md: 'md', ppt: 'office', pptx: 'office', doc: 'office', docx: 'office', xls: 'sheet', xlsx: 'sheet', ods: 'sheet' };
const TEXT = new Set('txt c cpp cc c++ h hpp java py js ts csv tsv json sql sh m r log xml yaml yml html css asm a51 v vhd ino tex s'.split(' '));
const kind = n => KIND[ext(n)] || (TEXT.has(ext(n)) ? 'text' : 'other');

const fmtSize = b => b < 1e3 ? b + ' B' : b < 1e6 ? (b / 1e3).toFixed(0) + ' KB' : b < 1e9 ? (b / 1e6).toFixed(1) + ' MB' : (b / 1e9).toFixed(2) + ' GB';
const fmtNum = n => n.toLocaleString('en-IN');
const fmtDate = t => !t ? '' : new Date(t * 1000).toLocaleDateString('en-GB', { month: 'short', year: 'numeric' });
const plural = (n, w) => `${fmtNum(n)} ${w}${n === 1 ? '' : 's'}`;
const href = p => '#/' + p.split('/').map(encodeURIComponent).join('/');
const fileUrl = f => S.base === null ? '#' : S.base + f.p.split('/').map(encodeURIComponent).join('/');
const pad = (n, w = 2) => String(n).padStart(w, '0');

/* ── index prep ─────────────────────────────────────── */
function prep(node, path, parent) {
  node.p = path; node.parent = parent; node.count = 0; node.size = 0; node.latest = 0;
  node.d.sort((a, b) => collator.compare(a.n, b.n));
  node.f = node.f.map(([n, s, m, meta]) => ({ n, s, m, meta, p: path ? path + '/' + n : n, parent: node, k: kind(n) }))
    .sort((a, b) => (a.n.startsWith('Z_') - b.n.startsWith('Z_')) || collator.compare(a.n, b.n));
  for (const f of node.f) { node.count++; node.size += f.s; node.latest = Math.max(node.latest, f.m); S.files.set(f.p, f); S.all.push(f); }
  for (const d of node.d) {
    prep(d, path ? path + '/' + d.n : d.n, node);
    node.count += d.count; node.size += d.size; node.latest = Math.max(node.latest, d.latest);
  }
  node.course = !!parent && (!!parseCourse(node.n) || node.d.some(d => isSection(d.n)));
  if (node.course) S.courses.push(node);
  S.nodes.set(path, node);
}
const ancestors = node => { const a = []; for (let n = node; n && n.parent; n = n.parent) a.unshift(n); return a; };
const courseOf = node => { for (let n = node; n; n = n.parent) if (n.course) return n; return null; };
const hasCourses = node => node.d.some(d => d.course);
const isTerm = n => /^Sem\d+$|-Sem$/.test(n);
const groupRank = n => n.startsWith('_') ? 2 : isTerm(n) ? 0 : 1;
function context(f) {
  return ancestors(f.parent).map(n => label(n.n)).slice(-3).join(' › ');
}

/* ── routing ────────────────────────────────────────── */
const EMBED = !!document.getElementById('sej-data');
const navStack = [];
function nav(p, replace = false) {
  if (!EMBED) { replace ? location.replace(href(p)) : (location.hash = href(p)); return; }
  if (S.files.has(p) || S.files.has(S.cur)) replace = true;
  if (!replace && S.cur !== p) navStack.push(S.cur || '');
  S.cur = p;
  route();
}
function goBack() { if (navStack.length) { S.cur = navStack.pop(); route(); } }
function currentPath() {
  if (EMBED) return S.cur || '';
  let h = location.hash.replace(/^#\/?/, '');
  try { h = decodeURIComponent(h); } catch { /* keep raw */ }
  return h.replace(/\/+$/, '');
}
function route() {
  const p = currentPath();
  if (p === 'contribute') { closePreview(); contributeView(); return; }
  if (p === 'credits') { closePreview(); creditsView(); return; }
  if (p === 'data') { closePreview(); dataView(); return; }
  if (p.startsWith('status/')) { closePreview(); statusView(p.slice(7)); return; }
  const f = S.files.get(p);
  if (f) {
    if (!S.preview) S.pushed = !EMBED && S.rendered !== null;  // did opening this file add a history entry we can step back over?
    render(courseOf(f.parent) || f.parent); openPreview(f); return;
  }
  closePreview();
  const node = S.nodes.get(p);
  if (!node) { notFound(p); return; }
  const course = courseOf(node);
  render(course || node);
  if (course && course !== node) {
    const sec = ancestors(node)[ancestors(course).length];
    S.spyPick?.('sec-' + sec.n);
    requestAnimationFrame(() => document.getElementById('sec-' + sec.n)?.scrollIntoView({ block: 'start' }));
  }
}

function notFound(p) {
  const key = '404:' + p;
  if (S.rendered === key) return;
  pageShell(key, 'Not found', `<header class="page-head"><div class="eyebrow">404</div><h1>Nothing at this address</h1>
    <p class="lede-sm">There is no folder or file called <code>${esc(p)}</code>. It may have been moved or renamed.</p>
    <p style="margin-top:22px"><a class="pill-btn solid" href="#/">Back to home</a>&nbsp; <button class="pill-btn" type="button" data-search>Search</button></p></header>`);
  $('#crumbs').innerHTML = '';
  $$('[data-search]', $('#view')).forEach(b => b.addEventListener('click', openPalette));
}

function render(node) {
  if (S.rendered === node) return;
  S.rendered = node;
  crumbs(node);
  const bb = $('#backBtn'); if (bb) bb.hidden = !EMBED || node === S.root;
  const v = $('#view');
  v.innerHTML = node === S.root ? homeView() : node.course ? courseView(node) : dirView(node);
  document.title = node === S.root ? 'Sadaqah-e-Jariyah' : `${label(node.n)} — Sadaqah-e-Jariyah`;
  window.scrollTo(0, 0);
  bindView(v);
}

function crumbs(node) {
  const a = ancestors(node);
  $('#crumbs').innerHTML = a.map(n => `<span class="sep">›</span><a href="${href(n.p)}">${esc(label(n.n))}</a>`).join('');
}

/* ── pieces ─────────────────────────────────────────── */
const extBadge = f => `<span class="ext k-${f.k}">${esc(ext(f.n).slice(0, 5) || '—')}</span>`;

const linkUrl = f => (f.k === 'link' && /^https:\/\/[^\s"<>]+$/.test(f.meta?.url || '')) ? f.meta.url : null;
const hostOf = u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return 'link'; } };
function fileRows(files, { ctx = false, limit = 0 } = {}) {
  const shown = limit ? files.slice(0, limit) : files;
  const rows = shown.map(f => {
    const url = linkUrl(f);  // link entries open the page they point to
    return `
    <a class="file" href="${url ? esc(url) : href(f.p)}"${url ? ' target="_blank" rel="noopener nofollow"' : ` data-p="${esc(f.p)}"`} title="${esc(url || f.n)}">
      ${extBadge(f)}
      <span class="fname">${esc(f.meta?.title || prettyFile(f.n))}${ctx ? `<small>${esc(context(f))}</small>` : ''}</span>
      <span class="fsize">${url ? esc(hostOf(url)) + ' ↗' : fmtSize(f.s)}</span>
      <span class="fdate">${fmtDate(f.m)}</span>
    </a>`;
  }).join('');
  const more = limit && files.length > limit
    ? `<div class="more">${fmtNum(files.length - limit)} more · <button type="button" data-more>show all</button></div>` : '';
  return `<div class="files">${rows}</div>${more}`;
}

function coverage(course) {
  const keys = ['Syllabus', 'Complete-Notes', 'Unit-1', 'Unit-2', 'Unit-3', 'Unit-4', 'Unit-5', 'PYQs-and-Assignments', 'Books-and-Resources'];
  const has = n => { const d = course.d.find(x => x.n === n); return d && d.count > 0; };
  const present = keys.filter(k => course.d.some(x => x.n === k) || k === 'Syllabus' || k === 'PYQs-and-Assignments');
  return `<span class="cov" title="${present.map(k => label(k) + (has(k) ? ' ✓' : ' —')).join('\n')}">${present.map(k => `<i class="${has(k) ? '' : 'o'}"></i>`).join('')}</span>`;
}

function courseRow(d, i) {
  const c = parseCourse(d.n);
  const codes = c ? c.codes : [];
  return `<a class="course${d.count ? '' : ' dim'}" href="${href(d.p)}">
    <span class="ci">${pad(i + 1)}</span>
    <span class="cc">${codes.length ? codes.map(x => `<i>${esc(x)}</i>`).join('') : '—'}</span>
    <span class="ct">${esc(label(d.n))}</span>
    ${coverage(d)}
    <span class="cn">${d.count ? plural(d.count, 'file') : 'empty'}</span>
    <span class="arr">→</span>
  </a>`;
}

function card(d, i, kindLabel, small = false) {
  const courses = S.courses.filter(c => c.p.startsWith(d.p + '/')).length;
  const sems = d.d.filter(x => hasCourses(x) && isTerm(x.n)).length;
  return `<a class="card hud${small ? ' small' : ''}" href="${href(d.p)}">
    <span class="scan"></span>
    <span class="idx">${small ? esc(kindLabel) : ''}</span>
    <h3>${esc(label(d.n))}</h3>
    <span class="go">→</span>
    <span class="sub">
      ${sems ? `<span><b>${sems}</b> semester${sems === 1 ? '' : 's'}</span>` : ''}
      ${courses ? `<span><b>${courses}</b> course${courses === 1 ? '' : 's'}</span>` : ''}
      <span><b>${fmtNum(d.count)}</b> files</span>
      <span>${fmtSize(d.size)}</span>
    </span>
  </a>`;
}

/* ── views ──────────────────────────────────────────── */
function homeView() {
  const r = S.root;
  const pyqs = S.all.filter(f => f.p.includes('/PYQs-and-Assignments/')).length;
  const videos = S.all.filter(f => f.k === 'video').length;
  const tops = r.d.filter(d => !d.n.startsWith('_'));
  const umbrellas = tops.filter(d => !d.course && !hasCourses(d) && d.d.some(x => !x.course && x.d.length));
  const branches = tops.filter(d => !umbrellas.includes(d));
  const others = r.d.filter(d => d.n.startsWith('_'));
  const recent = [...S.all].sort((a, b) => b.m - a.m).slice(0, 8);
  return `
  <section class="hero">
    <div class="ripples" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
    <div class="hero-reveal">
      <h1 class="display">Sadaqah-e-Jariyah</h1>
      <p class="arabic" lang="ar">صدقة جارية</p>
      <p class="lede">Notes, past papers, syllabi and lab work for every B.Tech semester at AMU, laid out so the next student never has to hunt for them.</p>
    </div>
    <button class="hero-search" type="button" data-search>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
      <span>Search ${fmtNum(r.count)} files</span><kbd>${isMac ? '⌘' : 'Ctrl'} K</kbd>
    </button>
  </section>
  <section class="stats">
    <div class="stat"><div class="num">${fmtNum(r.count)}</div><div class="cap">Files</div></div>
    <div class="stat"><div class="num">${S.courses.length}</div><div class="cap">Courses</div></div>
    <div class="stat"><div class="num">${fmtNum(pyqs)}</div><div class="cap">PYQs & Assignments</div></div>
    <div class="stat"><div class="num">${(r.size / 1e9).toFixed(1)}<small>GB</small></div><div class="cap">${fmtNum(videos)} lecture videos</div></div>
  </section>
  ${umbrellas.map(u => `<h2 class="label"><a href="${href(u.p)}">${esc(label(u.n))}</a></h2>
  <div class="cards">${u.d.filter(d => !d.n.startsWith('_')).sort(branchOrder).map((d, i) => card(d, i, 'Branch')).join('')}</div>`).join('')}
  ${branches.length ? `<h2 class="label">Branches</h2><div class="cards">${branches.map((d, i) => card(d, i, 'Branch')).join('')}</div>` : ''}
  ${others.length ? `<h2 class="label">Other</h2><div class="cards">${others.map((d, i) => card(d, i, 'Folder', true)).join('')}</div>` : ''}
  <a class="cta hud" href="#/contribute"><span class="eyebrow"><span class="pulse"></span> Contribute</span><span class="cta-t">Have notes, past papers or lab work? Drop them here and they’ll be sorted into the right course.</span><span class="go">→</span></a>
  <h2 class="label">Recently added</h2>
  ${fileRows(recent, { ctx: true })}`;
}

/* ── programmes (from the official curricula) ───────── */
const branchOrder = (a, b) => (a.n === 'First-Year-Common' ? -1 : b.n === 'First-Year-Common' ? 1 : 0) || (a.n === 'Shared-Courses') - (b.n === 'Shared-Courses') || collator.compare(label(a.n), label(b.n));
const progsOf = node => (S.pg?.programmes || []).filter(p => p.branch === node.p);
const progName = p => `${p.degree} ${p.name}`;
function pickedProg(node, list) {
  let id = null;
  try { id = localStorage.getItem('sej-prog:' + node.p); } catch { /* storage unavailable */ }
  return list.find(p => p.id === id) || list.find(p => p.published) || list[0];
}
// every code a course goes by — the folder's, the catalog's and its B.E. twins — with `first` (the programme's own) leading
function courseCodes(p, first) {
  const meta = S.pg?.courses?.[p] || {};
  const pc = parseCourse(p.split('/').pop());
  return [...new Set([first, ...(pc ? pc.codes : []), ...(meta.codes || []), ...(meta.be || [])].filter(Boolean))];
}
function progRow(it, i) {
  const node = S.nodes.get(it.p);
  const meta = S.pg.courses[it.p] || {};
  const codes = courseCodes(it.p, it.c);
  const title = node ? label(node.n) : (meta.title || it.p.split('/').pop());
  const inner = `<span class="ci">${pad(i + 1)}</span>
    <span class="cc">${codes.map(x => `<i>${esc(x)}</i>`).join('')}</span>
    <span class="ct">${esc(title)}${it.p.split('/')[1] === 'Shared-Courses' ? '<small>shared course</small>' : ''}</span>
    ${node && node.course ? coverage(node) : '<span class="cov"></span>'}
    <span class="cn">${node && node.count ? plural(node.count, 'file') : 'no files yet'}</span>
    <span class="arr">→</span>`;
  return node ? `<a class="course${node.count ? '' : ' dim'}" href="${href(it.p)}">${inner}</a>` : `<div class="course dim">${inner}</div>`;
}
function progPanel(node, p) {
  if (!p.published) {
    const twin = progsOf(node).find(x => x.published && x.degree === 'B.Tech');
    return `<div class="empty">The department hasn’t published the ${esc(progName(p))} curriculum yet, so its course list isn’t here. ${p.degree === 'B.E.' && twin
      ? `B.E. courses share their B.Tech twin’s folder (the B.E. code is the B.Tech code with an E in front), so open <b>${esc(progName(twin))}</b> above.`
      : 'Files can still be added to the semester folders below.'}</div>`;
  }
  const fy = S.nodes.get((S.pg.umbrella || '') + '/First-Year-Common');
  const listed = new Set();
  const sems = Object.entries(p.semesters).map(([sem, items]) => {
    items.forEach(it => listed.add(it.p));
    return `<section class="group">
      <div class="group-head"><span class="gi">S${pad(sem)}</span><h2>Semester ${sem}</h2><span class="gc">${plural(items.length, 'course')}</span></div>
      ${items.map((it, j) => progRow(it, j)).join('')}
    </section>`;
  }).join('');
  const official = p.official.map(x => S.files.get(x)).filter(Boolean);
  return `
  ${official.length ? `<div class="official">${official.map(f => `<a class="off" href="${href(f.p)}" data-p="${esc(f.p)}"><span class="ext k-pdf">pdf</span><span><b>${/curriculum/i.test(f.n) ? 'Curriculum' : 'Syllabus'}</b><small>Official · ${fmtSize(f.s)}</small></span></a>`).join('')}</div>` : ''}
  ${fy ? `<section class="group"><div class="group-head"><span class="gi">S01</span><h2>Semesters 1 & 2</h2><span class="gc">common to all branches</span></div>
    <a class="course" href="${href(fy.p)}"><span class="ci">01</span><span class="cc"><i>FY</i></span><span class="ct">First Year (Common)</span><span class="cov"></span><span class="cn">${plural(fy.count, 'file')}</span><span class="arr">→</span></a></section>` : ''}
  ${sems}`;
}

function dirView(node) {
  const progs = progsOf(node);
  if (progs.length) return branchView(node, progs);
  const groups = node.d.filter(hasCourses).sort((a, b) => groupRank(a.n) - groupRank(b.n) || collator.compare(a.n, b.n));
  const terms = groups.filter(g => isTerm(g.n)).length;
  const loose = node.d.filter(d => !hasCourses(d));
  const looseCourses = loose.filter(d => d.course);
  const folders = loose.filter(d => !d.course);
  const parent = node.parent && node.parent !== S.root ? label(node.parent.n) : 'B.Tech';
  let i = 0;
  return `
  <header class="page-head">
    <div class="eyebrow">${esc(parent)}</div>
    <h1>${esc(label(node.n))}</h1>
    <div class="meta"><span><b>${fmtNum(node.count)}</b> files</span><span>${fmtSize(node.size)}</span>${terms ? `<span><b>${terms}</b> semesters</span>` : ''}</div>
  </header>
  ${folders.length ? `<h2 class="label">Reference</h2><div class="cards">${folders.map((d, j) => card(d, j, 'Folder', true)).join('')}</div>` : ''}
  ${looseCourses.length ? `<section class="group"><div class="group-head"><span class="gi">—</span><h2>Courses</h2><span class="gc">${plural(looseCourses.length, 'course')}</span></div>${looseCourses.map((d, j) => courseRow(d, j)).join('')}</section>` : ''}
  ${groups.map(g => {
    const cs = g.d.filter(d => d.course), rest = g.d.filter(d => !d.course);
    const sem = g.n.match(/^Sem(\d+)$/);
    return `<section class="group">
      <div class="group-head"><span class="gi">${sem ? 'S' + pad(sem[1]) : pad(++i)}</span><h2><a href="${href(g.p)}">${esc(label(g.n))}</a></h2><span class="gc">${plural(cs.length, 'course')} · ${plural(g.count, 'file')}</span></div>
      ${cs.map((d, j) => courseRow(d, j)).join('')}
      ${rest.map((d, j) => courseRow(d, cs.length + j)).join('')}
      ${g.f.length ? fileRows(g.f) : ''}
    </section>`;
  }).join('')}
  ${node.f.length ? `<h2 class="label">Files <b>${node.f.length}</b></h2>${fileRows(node.f, { limit: 200 })}` : ''}
  ${!node.count && !node.d.length ? `<div class="empty">This folder is empty. Add files under <code>${esc(node.p)}/</code> and open a pull request.</div>` : ''}`;
}

function branchView(node, progs) {
  const p = pickedProg(node, progs);
  const inProgs = new Set(progs.flatMap(x => Object.values(x.semesters).flat().map(it => it.p)));
  // everything in the branch's folders that no programme lists (elective slots, extra electives, old subjects…)
  // a departmental elective (COE4610…) that is also another programme's core course lives in that semester's folder;
  // list it under Electives as well, so the branch's elective list stays complete
  const isElective = c => (parseCourse(c.n)?.codes || []).some(x => /^[A-Z]{2}E\d{4}$/.test(x));
  const elsewhere = g => S.courses.filter(c => c.p.startsWith(node.p + '/') && c.parent !== g && !c.p.includes('/_') && isElective(c));
  const extra = node.d.filter(d => !d.n.startsWith('_') || d.n === '_Old-Syllabus-Subjects').map(g => ({ g, cs: [...g.d.filter(d => d.course && !inProgs.has(d.p)), ...(g.n === 'Electives' ? elsewhere(g) : [])] })).filter(x => x.cs.length);
  const folders = node.d.filter(d => (d.n.startsWith('_') && d.n !== '_Old-Syllabus-Subjects') || (!d.course && !hasCourses(d) && !d.n.startsWith('_')));
  const nCourses = S.courses.filter(c => c.p.startsWith(node.p + '/')).length;
  return `
  <header class="page-head">
    <div class="eyebrow">${esc(node.parent && node.parent !== S.root ? label(node.parent.n) : 'B.Tech')}</div>
    <h1>${esc(label(node.n))}</h1>
    <div class="meta"><span><b>${fmtNum(node.count)}</b> files</span><span>${fmtSize(node.size)}</span><span><b>${nCourses}</b> courses</span></div>
  </header>
  <nav class="progs" aria-label="Programme">${progs.map(x => `<button type="button" class="prog${x === p ? ' on' : ''}" data-prog="${esc(x.id)}" aria-pressed="${x === p}"><b>${esc(x.degree)}</b> ${esc(x.name)}</button>`).join('')}</nav>
  <div id="progPanel">${progPanel(node, p)}</div>
  ${extra.map(({ g, cs }) => `<section class="group"><div class="group-head"><span class="gi">+</span><h2><a href="${href(g.p)}">${esc(label(g.n))}</a></h2><span class="gc">${plural(cs.length, 'course')}</span></div>${cs.map((d, j) => courseRow(d, j)).join('')}</section>`).join('')}
  ${folders.length ? `<h2 class="label">Folders</h2><div class="cards">${folders.map((d, j) => card(d, j, 'Folder', true)).join('')}</div>` : ''}`;
}

function flatten(dir, depth = 0) {
  // returns [{dir, depth}] for nested sub-folders, in order
  const out = [];
  for (const d of dir.d) { out.push({ dir: d, depth }); out.push(...flatten(d, depth + 1)); }
  return out;
}

function courseView(node) {
  const c = parseCourse(node.n);
  const anc = ancestors(node).slice(0, -1).map(n => `<a href="${href(n.p)}">${esc(label(n.n))}</a>`).join(' <span>·</span> ');
  const sections = [...node.d].sort((a, b) => sectionRank(a.n) - sectionRank(b.n) || collator.compare(a.n, b.n));
  const blocks = [];
  if (node.f.length) blocks.push({ id: 'files', name: 'Files', count: node.f.length, html: fileRows(node.f) });
  sections.forEach(s => {
    const subs = flatten(s).filter(x => x.dir.f.length);
    const html = s.count
      ? (s.f.length ? fileRows(s.f) : '') + subs.map(x => `<div class="sub-head">${esc(x.dir.p.slice(s.p.length + 1).split('/').map(label).join(' › '))} <span>· ${x.dir.f.length}</span></div>${fileRows(x.dir.f)}`).join('')
      : `<div class="empty">Nothing here yet. Drop files into <code>${esc(s.p)}/</code> and open a pull request.</div>`;
    blocks.push({ id: 'sec-' + s.n, name: label(s.n), count: s.count, html });
  });
  const filled = sections.filter(s => s.count).length;
  return `
  <header class="page-head">
    <div class="eyebrow">${anc}</div>
    ${(() => {
      const meta = S.pg?.courses?.[node.p];
      const codes = [...new Set([...(c ? c.codes : []), ...(meta?.codes || []), ...(meta?.be || [])])];
      return codes.length ? `<div class="codes" style="margin-top:22px">${codes.map(x => `<span class="code-chip">${esc(x)}</span>`).join('')}</div>` : '';
    })()}
    <h1>${esc(label(node.n))}</h1>
    <div class="meta"><span><b>${fmtNum(node.count)}</b> files</span><span>${fmtSize(node.size)}</span><span><b>${filled}</b>/${sections.length} sections filled</span>${node.latest ? `<span>updated ${fmtDate(node.latest)}</span>` : ''}</div>
    ${(() => {
      const meta = S.pg?.courses?.[node.p]; if (!meta) return '';
      const P = S.pg.programmes;
      const taught = Object.entries(meta.progs || {}).map(([id, sems]) => { const p = P.find(x => x.id === id); return p ? `${p.short.startsWith(p.degree) ? p.short : p.degree + ' ' + p.short}${sems.length ? ' · Sem ' + sems.join(', ') : ''}` : ''; }).filter(Boolean);
      return (taught.length ? `<p class="taught">Taught in ${esc(taught.join('  ·  '))}</p>` : '') + (meta.old?.length ? `<p class="taught">Older codes: ${esc(meta.old.join(', '))}</p>` : '');
    })()}
  </header>
  <nav class="secnav">${blocks.map(b => `<a class="chip${b.count ? '' : ' empty'}" href="#" data-jump="${esc(b.id)}">${esc(b.name)} <em>${b.count}</em></a>`).join('')}</nav>
  ${blocks.map((b, i) => `<section class="sec" id="${esc(b.id)}"><div class="sec-head"><span class="si">${pad(i + 1)}</span><h2>${esc(b.name)}</h2><span class="sc">${plural(b.count, 'file')}</span></div>${b.html}</section>`).join('')}`;
}

function bindView(v) {
  $$('[data-search]', v).forEach(b => b.addEventListener('click', openPalette));
  $$('[data-more]', v).forEach(b => b.addEventListener('click', () => {
    const node = S.rendered;
    const wrap = b.closest('.more');
    wrap.previousElementSibling.outerHTML = fileRows(node.f);
    wrap.remove();
  }));
  $$('[data-prog]', v).forEach(b => b.addEventListener('click', () => {
    const node = S.rendered, p = progsOf(node).find(x => x.id === b.dataset.prog);
    if (!p) return;
    try { localStorage.setItem('sej-prog:' + node.p, p.id); } catch { /* storage unavailable */ }
    $$('[data-prog]', v).forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', x === b); });
    $('#progPanel').innerHTML = progPanel(node, p);
    markCurrent();
  }));
  if (S.spyOff) { S.spyOff(); S.spyOff = null; }
  const chips = $$('[data-jump]', v);
  const secs = chips.map(c => document.getElementById(c.dataset.jump));
  if (chips.length && secs.every(Boolean)) {
    let picked = null, queued = false;   // `picked`: the chip the reader clicked, kept lit until they scroll by hand
    let lit = null, barTouched = 0;      // the chip lit last; when the reader last scrolled the chip row themselves
    const row = chips[0].parentElement;
    const markRow = () => { barTouched = Date.now(); };
    ['wheel', 'touchstart', 'pointerdown', 'scroll'].forEach(ev => row.addEventListener(ev, e => {
      if (ev !== 'scroll' || !row._auto) markRow();   // the row's own scrolling, unless we caused it
    }, { passive: true }));
    const light = id => {
      let on = null;
      chips.forEach(c => { const is = c.dataset.jump === id; c.classList.toggle('on', is); if (is) on = c; });
      if (!on || on === lit) return;
      lit = on;
      // bring the new chip into view inside its own row (scrollIntoView() would also move the page), but never
      // while the reader is scrolling the row by hand, and only if it isn't already visible
      if (Date.now() - barTouched < 2000) return;
      const r = on.getBoundingClientRect(), n = row.getBoundingClientRect();
      if (r.left >= n.left + 8 && r.right <= n.right - 8) return;
      row._auto = true;
      row.scrollBy({ left: (r.left + r.width / 2) - (n.left + n.width / 2), behavior: 'smooth' });
      setTimeout(() => { row._auto = false; }, 600);
    };
    const spy = () => {
      queued = false;
      if (picked) return light(picked);
      const bar = $('.secnav'); if (!bar) return;
      const line = bar.getBoundingClientRect().bottom + 24;   // just under the sticky bars
      const atEnd = innerHeight + scrollY >= document.documentElement.scrollHeight - 2;
      let cur = secs[0];
      for (const sec of secs) if (sec.getBoundingClientRect().top <= line) cur = sec;
      light(atEnd && scrollY > 0 ? secs[secs.length - 1].id : cur.id);
    };
    const queue = () => { if (!queued) { queued = true; requestAnimationFrame(spy); } };
    const release = e => { if (e && row.contains(e.target)) return; picked = null; queue(); };   // touching the chip row isn't scrolling the page
    const hand = ['wheel', 'touchstart', 'mousedown', 'keydown'];
    addEventListener('scroll', queue, { passive: true });
    addEventListener('resize', queue);
    hand.forEach(ev => addEventListener(ev, release, { passive: true, capture: true }));
    chips.forEach(ch => ch.addEventListener('click', e => {
      e.preventDefault();
      picked = ch.dataset.jump; light(picked);   // the row stays where the reader scrolled it
      document.getElementById(picked).scrollIntoView({ behavior: 'smooth', block: 'start' });
    }));
    S.spyPick = id => { picked = id; light(id); };
    S.spyOff = () => { removeEventListener('scroll', queue); removeEventListener('resize', queue); hand.forEach(ev => removeEventListener(ev, release, true)); S.spyPick = null; };
    spy();
  }
  markCurrent();
}

/* ── preview drawer ─────────────────────────────────── */
let previewToken = 0;
function openPreview(f) {
  if (f.k === 'link') { const u = linkUrl(f); if (u) window.open(u, '_blank', 'noopener'); history.back(); return; }
  S.preview = f;
  const sib = f.parent.f, i = sib.indexOf(f);
  $('#drExt').className = 'ext k-' + f.k;
  $('#drExt').textContent = ext(f.n).slice(0, 5) || '—';
  $('#drName').textContent = prettyFile(f.n);
  $('#drName').title = f.n;
  const by = f.meta ? [f.meta.author && 'notes by ' + f.meta.author, f.meta.professor && !(f.meta.author || '').includes(f.meta.professor) && 'taught by ' + f.meta.professor, f.meta.source && 'from ' + f.meta.source, f.meta.credit && f.meta.credit !== f.meta.author && 'shared by ' + f.meta.credit].filter(Boolean).join(' · ') : '';
  $('#drPath').textContent = `${context(f)}  ·  ${fmtSize(f.s)}`;
  $('#drBy').textContent = by ? by[0].toUpperCase() + by.slice(1) : '';
  $('#drBy').hidden = !by;
  $('#drBy').title = $('#drBy').textContent;
  // converted documents keep their original (.docx, .pptx …) in the dataset's history, at the release it was published in
  const orig = f.meta && f.meta.orig && S.base && /\/resolve\/main\/$/.test(S.base) ? f.meta.orig : '';
  $('#drOrig').hidden = !orig;
  if (orig) {
    const at = orig.indexOf('/');
    $('#drOrig').href = S.base.replace(/\/resolve\/main\/$/, '/resolve/' + orig.slice(0, at) + '/') + orig.slice(at + 1).split('/').map(encodeURIComponent).join('/') + '?download=true';
    $('#drOrig').textContent = 'Original ' + ext(orig).toUpperCase() + ' (before conversion to PDF)';
  }
  $('#drPos').textContent = `${i + 1} / ${sib.length}`;
  $('#prevBtn').disabled = i <= 0;
  $('#nextBtn').disabled = i >= sib.length - 1;
  const url = fileUrl(f);
  $('#openBtn').href = url;
  // the files live on Hugging Face, another site, where browsers ignore the download attribute (Brave and others
  // just open the PDF); ?download=true makes Hugging Face send it as an attachment instead
  $('#dlBtn').href = /^https?:/.test(url) ? url + '?download=true' : url;
  $('#dlBtn').setAttribute('download', f.n);
  $('#openBtn').hidden = $('#dlBtn').hidden = S.base === null;
  $('#drawer').classList.add('on');
  $('#drawer').inert = false;
  $('#drawer').setAttribute('aria-hidden', 'false');
  $('#scrim').classList.add('on');
  document.body.style.overflow = 'hidden';
  fillBody(f, url);
  markCurrent();
}
function closePreview() {
  if (!S.preview) return;
  S.preview = null;
  S.pushed = false;
  previewToken++;
  stopPdf();
  $('#drawer').classList.remove('on');
  $('#drawer').inert = true;
  $('#drawer').setAttribute('aria-hidden', 'true');
  $('#scrim').classList.remove('on');
  document.body.style.overflow = '';
  setTimeout(() => { if (!S.preview) $('#drBody').innerHTML = ''; }, 450);
  markCurrent();
}
function markCurrent() {
  $$('.file.cur').forEach(el => el.classList.remove('cur'));
  if (S.preview) $$('.file').filter(el => el.dataset.p === S.preview.p).forEach(el => el.classList.add('cur'));
}
function noPreview(f, url, msg) {
  return `<div class="nofile"><div><div class="big">.${esc(ext(f.n) || 'file')}</div><p>${msg}<br>${esc(f.n)} · ${fmtSize(f.s)}</p>
    <a class="pill-btn solid" href="${/^https?:/.test(url) ? url + '?download=true' : url}" download="${esc(f.n)}">Download</a>&nbsp; <a class="pill-btn" href="${url}" target="_blank" rel="noopener">Open ↗</a></div></div>`;
}
async function fillBody(f, url) {
  const body = $('#drBody');
  const token = ++previewToken;
  stopPdf();
  body.scrollTop = 0;
  if (S.base === null) {
    body.innerHTML = `<div class="nofile"><div><div class="big">.${esc(ext(f.n) || 'file')}</div><p>${esc(f.n)} · ${fmtSize(f.s)}<br><br>This file is stored on the laptop.<br>Files go online once the archive is moved to cloud storage.</p></div></div>`;
    return;
  }
  switch (f.k) {
    case 'pdf':
      if (NATIVE_PDF && f.s && f.s <= NATIVE_FETCH_MAX) nativePdf(f, url, body, token);
      else if (NATIVE_PDF) body.innerHTML = `<iframe src="${url}#view=FitH" title="${esc(f.n)}"></iframe>`;
      else renderPdf(f, url, body, token);
      return;
    case 'img': body.innerHTML = `<div class="media"><img src="${url}" alt="${esc(f.n)}"></div>`; return;
    case 'video': body.innerHTML = `<div class="media"><video src="${url}" controls preload="metadata" playsinline></video></div>`; return;
    case 'audio': body.innerHTML = `<div class="media"><audio src="${url}" controls></audio></div>`; return;
    case 'office': body.innerHTML = noPreview(f, url, 'Office files can’t be previewed in the browser.'); return;
    case 'sheet': renderSheet(f, url, body, token); return;
    case 'other': body.innerHTML = noPreview(f, url, 'No preview for this file type.'); return;
  }
  const limit = f.k === 'nb' ? 25e6 : 3e6;
  // notebooks and Markdown need the whole file; plain text and code over the limit show their beginning
  if (f.s > limit && f.k !== 'text') { body.innerHTML = noPreview(f, url, 'Too large to preview here.'); return; }
  const part = f.s > limit;
  body.innerHTML = `<div class="loading"><span class="spinner"></span> Loading…</div>`;
  let text;
  try {
    const r = await fetch(url, part ? { headers: { Range: `bytes=0-${TEXT_HEAD - 1}` } } : {});
    if (!r.ok) throw new Error(r.status);
    text = await r.text();
    if (part) text = text.slice(0, TEXT_HEAD);
  } catch (e) {
    if (token === previewToken) body.innerHTML = noPreview(f, url, 'Couldn’t load this file.');
    return;
  }
  if (token !== previewToken) return;
  if (f.k === 'nb') body.innerHTML = renderNotebook(text);
  else if (f.k === 'md') body.innerHTML = await renderMarkdown(text);
  else body.innerHTML = (part ? `<div class="part-note">Showing the first ${fmtSize(TEXT_HEAD)} of ${fmtSize(f.s)}. Download the file for all of it.</div>` : '')
    + `<pre class="code${/^(txt|md|csv|tsv|log)$/.test(ext(f.n)) ? ' wrap' : ''}">${esc(text)}</pre>`;
}
const TEXT_HEAD = 1e6;

/* ── spreadsheets: each sheet as a table (SheetJS, loaded the first time one is opened) ── */
const SHEET_ROWS = 1000;
let xlsxLib = null;
function loadXlsx() {
  return xlsxLib || (xlsxLib = new Promise((ok, bad) => {
    const s = document.createElement('script');
    s.src = new URL('vendor/sheetjs/xlsx.full.min.js', location.href).href;
    s.onload = () => ok(window.XLSX);
    s.onerror = () => { xlsxLib = null; bad(new Error('sheetjs')); };
    document.head.appendChild(s);
  }));
}
async function renderSheet(f, url, body, token) {
  if (f.s > 25e6) { body.innerHTML = noPreview(f, url, 'Too large to preview here.'); return; }
  body.innerHTML = `<div class="loading"><span class="spinner"></span> Loading…</div>`;
  let wb, X;
  try {
    const [lib, r] = await Promise.all([loadXlsx(), fetch(url)]);
    if (!r.ok) throw new Error(r.status);
    X = lib;
    wb = X.read(await r.arrayBuffer(), { type: 'array', cellDates: true, dense: true });
  } catch (e) {
    if (token === previewToken) body.innerHTML = noPreview(f, url, 'Couldn’t open this spreadsheet.');
    return;
  }
  if (token !== previewToken) return;
  const names = wb.SheetNames;
  const show = i => {
    const ws = wb.Sheets[names[i]];
    let note = '';
    if (ws['!ref']) {
      const rg = X.utils.decode_range(ws['!ref']);
      if (rg.e.r - rg.s.r + 1 > SHEET_ROWS) {
        note = `<div class="part-note">Showing the first ${SHEET_ROWS} of ${rg.e.r - rg.s.r + 1} rows. Download the file for all of it.</div>`;
        rg.e.r = rg.s.r + SHEET_ROWS - 1;
      }
      ws['!ref'] = X.utils.encode_range(rg);
    }
    const html = ws['!ref'] ? X.utils.sheet_to_html(ws, { header: '', footer: '' }) : '<p class="part-note">This sheet is empty.</p>';
    $('.sheetv-body', body).innerHTML = note + html;
    $$('.sheetv-tabs button', body).forEach((b, j) => b.classList.toggle('on', j === i));
  };
  body.innerHTML = `<div class="sheetv">${names.length > 1 ? `<div class="sheetv-tabs">${names.map((n, i) => `<button type="button" data-i="${i}">${esc(n)}</button>`).join('')}</div>` : ''}<div class="sheetv-body"></div></div>`;
  $$('.sheetv-tabs button', body).forEach(b => b.addEventListener('click', () => show(+b.dataset.i)));
  show(0);
}
/* ── PDF pages, drawn by pdf.js where the browser can't show a PDF inline (Android, iOS, tablets) ── */
const NATIVE_PDF = navigator.pdfViewerEnabled === true && !matchMedia('(pointer: coarse)').matches;
const PDFJS = new URL('vendor/pdfjs/', location.href).href;
let pdfjs = null, stopPdfFn = null;
/* Desktop: the site downloads the PDF itself (retrying if the connection drops, e.g.
   "A network change was detected"), then hands it to the browser's own viewer from memory.
   Very large PDFs still go straight to the viewer so they can start showing before they finish. */
const NATIVE_FETCH_MAX = 30e6;
async function nativePdf(f, url, body, token) {
  body.innerHTML = `<div class="loading"><span class="spinner"></span> <span>Opening PDF…</span></div>`;
  const say = t => { const el = body.querySelector('.loading span:last-child'); if (el) el.textContent = t; };
  const ctrl = new AbortController();
  stopPdfFn = () => ctrl.abort();
  let data = null;
  for (let attempt = 1; attempt <= 4 && !data; attempt++) {
    try {
      const r = await fetch(url, { signal: ctrl.signal });
      if (!r.ok) throw new Error(r.status);
      const total = +r.headers.get('content-length') || f.s || 0;
      const reader = r.body.getReader(), parts = [];
      let got = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        parts.push(value); got += value.length;
        if (total) say(`Opening PDF… ${Math.min(99, Math.round(got / total * 100))}%`);
      }
      data = new Blob(parts, { type: 'application/pdf' });
    } catch (e) {
      if (ctrl.signal.aborted || token !== previewToken) return;
      if (attempt === 4) { body.innerHTML = noPreview(f, url, 'Couldn’t load this PDF. Check your connection and try again.'); return; }
      say('Connection dropped — retrying…');
      await new Promise(res => setTimeout(res, 1000 * attempt));
      if (ctrl.signal.aborted || token !== previewToken) return;
    }
  }
  if (token !== previewToken) return;
  const blobUrl = URL.createObjectURL(data);
  stopPdfFn = () => URL.revokeObjectURL(blobUrl);
  body.innerHTML = `<iframe src="${blobUrl}#view=FitH" title="${esc(f.n)}"></iframe>`;
}
function stopPdf() { if (stopPdfFn) { const f = stopPdfFn; stopPdfFn = null; f(); } }
async function renderPdf(f, url, body, token) {
  body.innerHTML = `<div class="loading"><span class="spinner"></span> <span>Opening PDF…</span></div>`;
  const say = t => { const el = body.querySelector('.loading span:last-child'); if (el) el.textContent = t; };
  let doc;
  try {
    if (!pdfjs) {
      pdfjs = await import(PDFJS + 'pdf.min.mjs');
      pdfjs.GlobalWorkerOptions.workerSrc = PDFJS + 'pdf.worker.min.mjs';
    }
    if (token !== previewToken) return;
    const task = pdfjs.getDocument({ url: new URL(url, location.href).href, cMapUrl: PDFJS + 'cmaps/', cMapPacked: true,
      standardFontDataUrl: PDFJS + 'standard_fonts/', disableAutoFetch: true, isEvalSupported: false });
    task.onProgress = ({ loaded, total }) => { if (total && loaded < total) say(`Opening PDF… ${Math.round(loaded / total * 100)}%`); };
    stopPdfFn = () => task.destroy();
    doc = await task.promise;
  } catch (e) {
    if (token === previewToken) body.innerHTML = noPreview(f, url, 'This PDF couldn’t be shown here.');
    return;
  }
  if (token !== previewToken) return;

  const v1 = (await doc.getPage(1)).getViewport({ scale: 1 });
  if (token !== previewToken) return;
  const wrap = document.createElement('div');
  wrap.className = 'pdfv';
  const pages = [];
  for (let n = 1; n <= doc.numPages; n++) {
    const el = document.createElement('div');
    el.className = 'pdfp';
    el.dataset.n = n;
    el.style.aspectRatio = `${v1.width} / ${v1.height}`;
    wrap.appendChild(el); pages.push(el);
  }
  const counter = document.createElement('div');
  counter.className = 'pdfn';
  counter.textContent = `1 / ${doc.numPages}`;
  body.replaceChildren(wrap, counter);

  const busy = new Set();
  const draw = async el => {
    const n = +el.dataset.n;
    if (el.firstChild || busy.has(n)) return;
    busy.add(n);
    try {
      const page = await doc.getPage(n);
      if (token !== previewToken || !el._want) return;
      const base = page.getViewport({ scale: 1 });
      el.style.aspectRatio = `${base.width} / ${base.height}`;
      const vp = page.getViewport({ scale: el.clientWidth / base.width * Math.min(devicePixelRatio || 1, 2) });
      const c = document.createElement('canvas');
      c.width = Math.floor(vp.width); c.height = Math.floor(vp.height);
      el._task = page.render({ canvasContext: c.getContext('2d'), viewport: vp });
      await el._task.promise;
      if (token === previewToken && el._want) el.replaceChildren(c);
    } catch { /* cancelled while scrolling past, or a broken page */ } finally {
      busy.delete(n); el._task = null;
      if (token === previewToken && el._want && !el.firstChild) requestAnimationFrame(() => draw(el));
    }
  };
  // draw pages near the screen, free the ones far away (long PDFs would otherwise eat the phone's memory)
  const io = new IntersectionObserver(entries => entries.forEach(en => {
    const el = en.target;
    el._want = en.isIntersecting;
    if (el._want) draw(el);
    else { el._task?.cancel(); const c = el.firstChild; if (c) { c.width = c.height = 0; el.replaceChildren(); } }
  }), { root: body, rootMargin: '150% 0px' });
  const seen = new IntersectionObserver(entries => entries.forEach(en => {
    if (en.isIntersecting) counter.textContent = `${en.target.dataset.n} / ${doc.numPages}`;
  }), { root: body, rootMargin: '-45% 0px -45% 0px' });
  pages.forEach(p => { io.observe(p); seen.observe(p); });
  stopPdfFn = () => { io.disconnect(); seen.disconnect(); doc.destroy(); };
}

function renderNotebook(text) {
  let nb;
  try { nb = JSON.parse(text); } catch { return `<pre class="code">${esc(text)}</pre>`; }
  const cells = nb.cells || (nb.worksheets && nb.worksheets[0] && nb.worksheets[0].cells) || [];
  const src = s => Array.isArray(s) ? s.join('') : (s || '');
  return `<div class="nb">${cells.map(c => {
    const s = src(c.source ?? c.input);
    if (c.cell_type === 'markdown' || c.cell_type === 'raw') return `<div class="nb-cell nb-md">${esc(s)}</div>`;
    const outs = (c.outputs || []).map(o => {
      const d = o.data || {};
      if (d['image/png']) return `<img src="data:image/png;base64,${src(d['image/png']).replace(/\s/g, '')}" alt="">`;
      if (d['image/jpeg']) return `<img src="data:image/jpeg;base64,${src(d['image/jpeg']).replace(/\s/g, '')}" alt="">`;
      if (o.text) return esc(src(o.text));
      if (d['text/plain']) return esc(src(d['text/plain']));
      if (o.ename) return esc(`${o.ename}: ${o.evalue}`);
      return '';
    }).join('');
    return `<div class="nb-cell"><div class="nb-in"><span>[${c.execution_count ?? ' '}]</span><pre>${esc(s)}</pre></div>${outs ? `<div class="nb-out">${outs}</div>` : ''}</div>`;
  }).join('')}</div>`;
}
let mdLib = null;
function loadScript(src) {
  return new Promise((ok, bad) => { const s = document.createElement('script'); s.src = src; s.onload = ok; s.onerror = bad; document.head.appendChild(s); });
}
async function renderMarkdown(text) {
  try {
    if (!mdLib) {
      await loadScript('https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js');
      await loadScript('https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js');
      mdLib = true;
    }
    return `<article class="prose">${window.DOMPurify.sanitize(window.marked.parse(text))}</article>`;
  } catch {
    return `<pre class="code wrap">${esc(text)}</pre>`;
  }
}
function step(delta) {
  if (!S.preview) return;
  const sib = S.preview.parent.f, i = sib.indexOf(S.preview) + delta;
  if (i >= 0 && i < sib.length) nav(sib[i].p, true);
}

/* ── search ─────────────────────────────────────────── */
const norm = s => s.toLowerCase().replace(/[_\-/.·]+/g, ' ');
let searchItems = null, results = [], active = 0;
function buildSearch() {
  searchItems = [
    ...S.courses.map(c => { const m = S.pg?.courses?.[c.p]; const alt = m ? [...m.codes, ...m.be, ...m.old].join(' ') : ''; return { type: 'course', node: c, hay: norm(c.p + ' ' + alt + ' ' + label(c.n) + ' ' + ancestors(c).map(a => label(a.n)).join(' ')), name: norm(c.n + ' ' + alt) }; }),
    ...S.all.map(f => ({ type: 'file', f, hay: norm(f.p + ' ' + ancestors(f.parent).map(a => label(a.n)).join(' ')), name: norm(f.n) })),
  ];
}
// words students type vs. the words used in folder names
const ALIAS = { maths: ['math'], dbms: ['database management'], dsa: ['data structure'], os: ['operating system'], cn: ['computer network'],
  coa: ['computer organi'], oop: ['object oriented'], toc: ['theory of computation'], dsp: ['digital signal'], ml: ['machine learning'], ai: ['artificial intelligence'] };
function search(q) {
  const toks = norm(q).split(/\s+/).filter(Boolean);
  if (!toks.length) return [];
  const out = [];
  for (const it of searchItems) {
    let score = 0, ok = true;
    for (const t0 of toks) {
      const alts = [t0, ...(ALIAS[t0] || [])];
      const t = alts.find(a => it.hay.includes(a));
      if (t === undefined) { ok = false; break; }
      if (it.name.includes(t)) score += 4;
      if (new RegExp('(^|\\s)' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).test(it.name)) score += 2;
    }
    if (!ok) continue;
    if (it.type === 'course') score += 6;
    score -= (it.type === 'file' ? it.f.p : it.node.p).length / 200;
    out.push([score, it]);
  }
  out.sort((a, b) => b[0] - a[0]);
  return out.slice(0, 60).map(x => x[1]);
}
function highlight(text, q) {
  const toks = norm(q).split(/\s+/).filter(t => t.length > 1).map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!toks.length) return esc(text);
  // split the raw text, then escape each piece, so tokens like "amp" can never match inside an &amp; entity
  return text.split(new RegExp('(' + toks.join('|') + ')', 'gi')).map((part, i) => i % 2 ? `<mark>${esc(part)}</mark>` : esc(part)).join('');
}
function renderResults() {
  const q = $('#q').value.trim();
  const box = $('#results');
  if (!q) {
    const picks = S.courses.filter(c => c.count).sort((a, b) => b.latest - a.latest).slice(0, 6);
    results = picks.map(node => ({ type: 'course', node }));
    active = 0;
    box.innerHTML = `<div class="pal-group">Recently updated courses</div>` + results.map((r, i) => resRow(r, i, '')).join('');
    return;
  }
  results = search(q);
  active = 0;
  box.innerHTML = results.length ? results.map((r, i) => resRow(r, i, q)).join('') : `<div class="pal-empty">Nothing matches “${esc(q)}”.</div>`;
}
function resRow(r, i, q) {
  if (r.type === 'course') {
    const codes = courseCodes(r.node.p);
    return `<div class="res${i === active ? ' on' : ''}" data-i="${i}"><span class="ext k-dir" aria-label="Course"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></span>
      <span class="rn">${highlight(label(r.node.n), q)}<span class="rc">${esc(codes.length ? codes.join(' / ') + ' · ' : '')}${esc(ancestors(r.node).slice(0, -1).map(a => label(a.n)).join(' › '))}</span></span>
      <span class="rs">${plural(r.node.count, 'file')}</span></div>`;
  }
  const f = r.f;
  return `<div class="res${i === active ? ' on' : ''}" data-i="${i}">${extBadge(f)}
    <span class="rn">${highlight(prettyFile(f.n), q)}<span class="rc">${esc(context(f))}</span></span>
    <span class="rs">${fmtSize(f.s)}</span></div>`;
}
function setActive(i) {
  if (!results.length) return;
  active = (i + results.length) % results.length;
  $$('.res').forEach(el => el.classList.toggle('on', +el.dataset.i === active));
  $(`.res[data-i="${active}"]`)?.scrollIntoView({ block: 'nearest' });
}
function choose(i) {
  const r = results[i];
  if (!r) return;
  closePalette();
  nav(r.type === 'course' ? r.node.p : r.f.p);
}
function openPalette() {
  if (!searchItems) buildSearch();
  $('#palette').hidden = false;
  $('#q').value = '';
  renderResults();
  setTimeout(() => $('#q').focus(), 10);
}
function closePalette() { $('#palette').hidden = true; }

/* ── contribute & status ────────────────────────────── */
const C = {
  ALLOWED: new Set(['pdf', 'png', 'jpg', 'jpeg', 'webp', 'heic', 'heif', 'gif', 'ppt', 'doc', 'xls', 'rtf', 'mp4', 'mov', 'webm', 'mkv',
    'pptx', 'docx', 'xlsx', 'odt', 'odp', 'ods', 'pages', 'key', 'numbers', 'zip',
    'txt', 'md', 'c', 'cpp', 'h', 'hpp', 'java', 'py', 'ipynb', 'csv', 'asm', 's', 'v', 'sv', 'vhd', 'vhdl', 'circ', 'm', 'sql',
    'js', 'ts', 'json', 'xml', 'tex', 'r', 'cs', 'go', 'rs', 'kt', 'sh', 'pl', 'lab', 'a51', 'ms14', 'ms13', 'ms12', 'pdsprj', 'dsn']),
  MAX_FILE: 500 * 1024 * 1024, MAX_FILES: 1000,
  files: [], tsToken: '', tsWidget: null, busy: false, pollTimer: null,
};
const STAGES = [
  ['received', 'Received', 'Your files are safely stored, waiting to be sorted.'],
  ['sorting', 'Sorting', 'The files are being read and matched to the right course.'],
  ['in_review', 'In review', 'A maintainer is checking the placement before it goes live.'],
  ['published', 'Published', 'Live on the site. Thank you!'],
];
function mine() { try { return JSON.parse(localStorage.getItem('sej-mine') || '[]'); } catch { return []; } }
function remember(id) {
  try { const m = mine().filter(x => x.id !== id); m.unshift({ id, t: Date.now() }); localStorage.setItem('sej-mine', JSON.stringify(m.slice(0, 30))); } catch { /* storage unavailable */ }
}
function pageShell(key, crumbLabel, html) {
  S.rendered = key;
  if (S.spyOff) { S.spyOff(); S.spyOff = null; }
  $('#crumbs').innerHTML = `<span class="sep">›</span><a href="${href(key)}">${esc(crumbLabel)}</a>`;
  const bb = $('#backBtn'); if (bb) bb.hidden = !EMBED;
  $('#view').innerHTML = html;
  document.title = `${crumbLabel} — Sadaqah-e-Jariyah`;
  window.scrollTo(0, 0);
}

// Where the material came from: every source we collected from, and the ones not collected yet
function creditsView() {
  const host = u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return ''; } };
  const row = x => `<li class="src-row">${x.u ? `<a class="src-t" href="${esc(x.u)}" target="_blank" rel="noopener">${esc(x.t)} <span class="src-h">${esc(host(x.u))} ↗</span></a>` : `<span class="src-t">${esc(x.t)}</span>`}
    ${x.c ? `<div class="src-c">${esc(x.c)}</div>` : ''}${x.w ? `<div class="src-w">${esc(x.w)}</div>` : ''}</li>`;
  const group = (title, note, xs) => xs.length ? `<h2 class="label">${title} <b>${xs.length}</b></h2>${note ? `<p class="lede-sm">${note}</p>` : ''}<ul class="src-list">${xs.map(row).join('')}</ul>` : '';
  const all = S.sources || [];
  pageShell('credits', 'Sources & credits', `<header class="page-head"><div class="eyebrow">Sources & credits</div><h1>Where everything came from</h1>
    <p class="lede-sm">Nearly everything here was shared by students, teachers and the Engineering College Library. These are the collections it was gathered from, with the people who made them. Each file also shows its own credit when you open it.</p></header>
    ${group('In the archive', '', all.filter(x => x.s === 'in'))}
    ${group('Not collected yet', 'Other AMU collections worth knowing about. Their material is not (or not yet) in this archive, so visit them directly.', all.filter(x => x.s !== 'in'))}`);
}

function dataView() {
  const base = S.base && /^https?:/.test(S.base) ? S.base : '';
  const m = base.match(/huggingface\.co\/datasets\/([^/]+\/[^/]+)\/resolve\//);
  const repo = m ? m[1] : '';
  const dataset = repo ? `https://huggingface.co/datasets/${repo}` : '';
  const site = FAST || location.origin + location.pathname.replace(/[^/]*$/, '');
  let n = 0, bytes = 0;
  S.files.forEach(f => { if (f.k !== 'link') { n++; bytes += f.s || 0; } });
  const code = t => `<pre class="code wrap api-code">${esc(t)}</pre>`;
  pageShell('data', 'Download & API', `<header class="page-head"><div class="eyebrow">Download &amp; API</div><h1>Take it all, or build on it</h1>
    <p class="lede-sm">Everything here is free. You can download the whole archive, or have your app read it directly, so you don't need to keep a copy that goes out of date.</p></header>
    <section class="api-sec">
      <h2 class="label">Download everything</h2>
      <p class="lede-sm">${n.toLocaleString()} files, ${fmtSize(bytes)}, kept on Hugging Face with full version history${dataset ? `: <a href="${esc(dataset)}" target="_blank" rel="noopener">${esc(dataset.replace('https://', ''))} ↗</a>` : ''}.</p>
      ${repo ? code(`pip install -U huggingface_hub

# everything
huggingface-cli download ${repo} --repo-type dataset --local-dir sej-files

# one branch, semester or course
huggingface-cli download ${repo} --repo-type dataset --local-dir sej-files \\
  --include "BTech-and-BE/Computer-Engineering/Sem5/*"`) : ''}
    </section>
    <section class="api-sec">
      <h2 class="label">API</h2>
      <p class="lede-sm">Read-only JSON, no key needed, open to any website or app (CORS enabled). It updates whenever the archive does.</p>
      <table class="api-tbl"><tbody>${[
        ['api/v1/files.json', 'Every file: path, name, size, modified, url, branch, semester, course (codes, title), section and credits (author, professor, year, shared by). Every link (videos, playlists, project pages) the same way.'],
        ['api/v1/programmes.json', 'Degrees, branches and their semester-wise course lists (the site’s menus).'],
        ['api/v1/sources.json', 'The collections the archive was gathered from, with credits.'],
        ['index.json', 'The folder tree exactly as this site loads it.'],
        ['api/v1/stats.json', 'How much the API has been used in the last 30 days.'],
      ].map(([u, d]) => `<tr><td><a href="${esc(site + u)}" target="_blank" rel="noopener"><span class="api-get">GET</span> /${esc(u)}</a></td><td>${d}</td></tr>`).join('')}</tbody></table>
      <p class="lede-sm">Together these hold everything this website is built from, so another app can rebuild it, or something new: an AI teacher that answers from a course’s notes and past papers and cites the exact file and page (retrieval-augmented generation), a question-paper builder, a course planner, a search tool. The site's own code is open source (GPL-3.0).</p>
      <p class="lede-sm" id="apiUse"></p>
      ${code(`const r = await fetch('${site}api/v1/files.json');
const { files } = await r.json();
const papers = files.filter(f => f.section === 'PYQs-and-Assignments' && f.course?.codes.includes('COC3092'));
// each file downloads from f.url`)}
      <p class="lede-sm">File URLs point at the latest version. To pin a fixed version, replace <code>/resolve/main/</code> with <code>/resolve/&lt;release&gt;/</code>, using a release tag from the dataset${dataset ? ` (<a href="${esc(dataset)}/tree/main" target="_blank" rel="noopener">browse versions ↗</a>)` : ''}.</p>
      <p class="lede-sm">Please fetch <code>files.json</code> once and cache it rather than on every page view, and mention <b>Sadaqah-e-Jariyah</b> and each file's own credit (its <code>meta</code>) where you show the material. Hugging Face limits heavy anonymous use, so a high-traffic app should use a Hugging Face token.</p>
    </section>`);
  fetch(FAST + 'api/v1/stats.json', { cache: 'no-store' }).then(r => r.ok ? r.json() : null).then(j => {
    const el = $('#apiUse');
    if (el && j && j.calls) el.textContent = `API use in the last 30 days: ${j.calls.toLocaleString()} calls from ${j.clients.toLocaleString()} ${j.clients === 1 ? 'app' : 'apps'}.`;
  }).catch(() => {});
}

function contributeView() {
  if (S.rendered === 'contribute') return;
  clearInterval(C.pollTimer);
  C.files = []; C.tsToken = ''; C.tsWidget = null;
  const live = !!S.api;
  const courseOptions = S.courses.filter(c => parseCourse(c.n)).map(c => {
    return `<option value="${esc(`${label(c.n)} (${courseCodes(c.p).join('/')}, ${label(c.parent.n)})`)}"></option>`;
  }).join('');
  const prev = mine();
  pageShell('contribute', 'Contribute', `
  <header class="page-head">
    <div class="eyebrow"><span class="pulse"></span> Contribute</div>
    <h1>Add to the archive</h1>
    <p class="lede-sm">Drop notes, past papers, syllabi or lab work. Each file is read and placed under the right course, then checked by a maintainer before it goes live. You don't need an account.</p>
  </header>
  ${live ? '' : `<div class="notice">Contributions aren’t open yet. Soon you’ll be able to drop files here.</div>`}
  <form class="cform" id="cform" novalidate>
    <section class="step">
      <div class="step-n">01</div>
      <div class="step-body">
        <h2>Files</h2>
        <label class="drop hud" id="drop" for="fileInput">
          <input type="file" id="fileInput" multiple hidden>
          <input type="file" id="folderInput" webkitdirectory multiple hidden>
          <span class="drop-main">Drop files or whole folders here, or <u>choose files</u></span>
          <span class="drop-sub">Anything: PDFs, photos, slides, Word, zips, videos, code · messy names are fine · up to 500 MB each</span>
        </label>
        <button type="button" class="pill-btn folder-btn" id="folderBtn">Choose a folder</button>
        <div class="flist" id="flist"></div>
        <label class="field"><span>Or links <em>one per line · Google Drive folders and files (set to “Anyone with the link”), YouTube, websites</em></span>
          <textarea id="links" rows="2" placeholder="https://drive.google.com/drive/folders/…"></textarea></label>
      </div>
    </section>
    <section class="step">
      <div class="step-n">02</div>
      <div class="step-body">
        <h2>What is it?</h2>
        <label class="field"><span>Course <em>optional, helps sorting</em></span>
          <input id="course" list="courseList" autocomplete="off" placeholder="Type a name or code: DBMS, COC2122, Applied Maths…">
          <datalist id="courseList">${courseOptions}</datalist></label>
        <label class="field"><span>Describe the files <em>optional · course, year, teacher, where they came from (e.g. AMU Cloud)</em></span>
          <textarea id="context" rows="4" maxlength="4000" placeholder="Mid-sem 1 paper 2024-25 with solutions, taught by Prof. X. The second file is Unit 3 class notes."></textarea></label>
        <div class="quick" id="quick">${['Past paper', 'Solutions', 'Assignment', 'Class notes', 'Slides', 'Syllabus', 'Lab record', 'Lecture video'].map(q => `<button type="button" class="chip">${q}</button>`).join('')}</div>
      </div>
    </section>
    <section class="step">
      <div class="step-n">03</div>
      <div class="step-body">
        <h2>Send</h2>
        <label class="field"><span>Credit me as <em>optional · leave blank to stay anonymous</em></span>
          <input id="credit" maxlength="80" placeholder="Your name"></label>
        <label class="check"><input type="checkbox" id="consent">
          <span>I’m allowed to share these: my own notes and solutions, class handouts, slides, papers, syllabi or lab work.
            No books or paid material, nothing with other people’s personal details, and no answers to work that’s still due.</span></label>
        <details class="rules"><summary>Contribution rules</summary><ul>
          <li><b>Piracy is haram:</b> no textbooks, chapters, solution manuals or paid course material.</li>
          <li><b>No one else’s personal details:</b> enrolment or faculty numbers, phone numbers, marks, ID cards, filled-in forms.</li>
          <li><b>No answers to work that’s still due.</b> Solutions only after the deadline.</li>
          <li><b>From AMU Cloud, AMU Pedia or a library site?</b> Say so above; the source is credited, and permission is yours to get.</li>
          <li><b>Don’t tidy anything.</b> Names, folders and formats are sorted for you; slides and documents are turned into PDFs.</li>
          <li>Your own work is shared under CC BY 4.0 with the credit you choose. Anything can be taken down on request.</li>
        </ul></details>
        <div id="ts" class="ts"></div>
        <div class="err" id="cerr" role="alert" hidden></div>
        <button class="submit" id="csubmit" type="submit" ${live ? '' : 'disabled'}>Send contribution <span>→</span></button>
        <p class="fine">Your description and file names appear publicly on GitHub, where the archive is maintained.</p>
      </div>
    </section>
  </form>
  <section class="cprog" id="cprog" hidden></section>
  ${prev.length ? `<h2 class="label">Your contributions</h2><div class="files">${prev.map(p => `<a class="file" href="${href('status/' + p.id)}"><span class="ext k-dir">SUB</span><span class="fname">${esc(p.id)}<small>sent ${new Date(p.t).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</small></span><span class="fsize"></span><span class="fdate">status →</span></a>`).join('')}</div>` : ''}
  `);
  bindContribute(live);
}

function renderFileList() {
  if (C.files.length <= C.MAX_FILES) showErr('');
  const total = C.files.reduce((a, f) => a + f.size, 0);
  $('#flist').innerHTML = C.files.map((f, i) => `
    <div class="frow${f.err ? ' bad' : ''}">
      <span class="ext k-${kind(f.name)}">${esc(ext(f.name).slice(0, 5) || '—')}</span>
      <span class="fname">${esc(fname(f))}${f.err ? `<small>${esc(f.err)}</small>` : ''}</span>
      <span class="fsize">${fmtSize(f.size)}</span>
      <button type="button" class="icon-btn sm" data-rm="${i}" title="Remove">✕</button>
    </div>`).join('') + (C.files.length ? `<div class="fsum">${plural(C.files.length, 'file')} · ${fmtSize(total)}</div>` : '');
  $$('[data-rm]', $('#flist')).forEach(b => b.addEventListener('click', () => { C.files.splice(+b.dataset.rm, 1); renderFileList(); }));
}
const fname = f => f.sejPath || f.webkitRelativePath || f.name;
function addFiles(list) {
  for (const f of list) {
    if (/(^|\/)(\.|__MACOSX\/|Thumbs\.db$|desktop\.ini$)/.test(fname(f))) continue;  // system files
    if (C.files.some(x => fname(x) === fname(f) && x.size === f.size)) continue;
    let err = '';
    if (!C.ALLOWED.has(ext(f.name))) err = `.${ext(f.name) || '?'} files aren't accepted`;
    else if (f.size > C.MAX_FILE) err = 'Larger than 500 MB. Share it as a Drive link instead.';
    else if (!f.size) err = 'File is empty';
    f.err = err;
    C.files.push(f);
  }
  if (C.files.length > C.MAX_FILES) showErr(`Up to ${C.MAX_FILES} files at a time. Send the rest as a second contribution.`);
  renderFileList();
}
// walk dropped folders (Chrome, Edge, Firefox, Safari all support webkitGetAsEntry)
async function droppedFiles(dt) {
  const items = [...(dt?.items || [])].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (!items.length) return [...(dt?.files || [])];
  const out = [];
  const walk = async (entry, path) => {
    if (entry.isFile) {
      const f = await new Promise((res, rej) => entry.file(res, rej));
      if (path) f.sejPath = path + f.name;
      out.push(f);
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      for (;;) {
        const batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        if (!batch.length) break;
        for (const e of batch) await walk(e, path + entry.name + '/');
      }
    }
  };
  for (const e of items) await walk(e, '');
  return out;
}
// Visit count from the Pages Function (functions/api/visitors.js). Asked for as soon as the script runs, so it
// arrives with the index; the last known count shows at once meanwhile. Not there on the local server: nothing shown.
const VISITS = fetch(FAST + 'api/visitors', { cache: 'no-store' }).then(r => r.ok ? r.json() : {}).then(j => j.count).catch(() => null);
function showVisitors() {
  const el = $('#footVisits');
  const put = n => { if (Number.isFinite(n) && n > 0) { el.textContent = plural(n, 'visit'); el.hidden = false; } };
  try { put(+localStorage.getItem('sej.visits')); } catch { /* storage blocked */ }
  // a count that can't be refreshed isn't shown: a stale number from this device's cache would look stuck
  VISITS.then(n => { if (Number.isFinite(n) && n > 0) { put(n); try { localStorage.setItem('sej.visits', n); } catch { /* storage blocked */ } } else el.hidden = true; });
}
function showErr(msg) { const e = $('#cerr'); if (!e) return; e.textContent = msg; e.hidden = !msg; if (msg) e.scrollIntoView({ block: 'center', behavior: 'smooth' }); }

function loadTurnstile() {
  if (!S.turnstile || !$('#ts')) return;
  const render = () => {
    if (!$('#ts') || C.tsWidget !== null) return;
    C.tsWidget = window.turnstile.render('#ts', {
      sitekey: S.turnstile, theme: 'light', size: 'flexible',
      callback: t => { C.tsToken = t; }, 'expired-callback': () => { C.tsToken = ''; },
      'error-callback': () => { C.tsToken = ''; showErr('The human check couldn’t load. Turn off ad-blockers for this page or try another browser.'); },
    });
  };
  if (window.turnstile) return render();
  window.sejTurnstileReady = render;
  if (!document.getElementById('ts-script')) {
    const s = document.createElement('script');
    s.id = 'ts-script'; s.async = true; s.defer = true;
    s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=sejTurnstileReady';
    document.head.appendChild(s);
  }
}

function bindContribute(live) {
  const drop = $('#drop'), input = $('#fileInput');
  input.addEventListener('change', () => { addFiles([...input.files]); input.value = ''; });
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', async e => addFiles(await droppedFiles(e.dataTransfer)));
  const folder = $('#folderInput');
  $('#folderBtn').addEventListener('click', () => folder.click());
  folder.addEventListener('change', () => { addFiles([...folder.files]); folder.value = ''; });
  $$('#quick .chip').forEach(b => b.addEventListener('click', () => {
    const t = $('#context'); t.value = (t.value.trim() ? t.value.trim() + ', ' : '') + b.textContent.toLowerCase(); t.focus();
  }));
  $('#cform').addEventListener('submit', e => { e.preventDefault(); if (live) submitContribution(); });
  if (live) loadTurnstile();
}

async function api(path, opts = {}) {
  let r;
  try { r = await fetch(S.api + path, opts); } catch { throw new Error('No connection. Check your internet and try again.'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || `Request failed (${r.status}).`), { status: r.status });
  return data;
}
function putPart(url, token, blob, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('PUT', url);
    x.setRequestHeader('Authorization', 'Bearer ' + token);
    x.upload.onprogress = e => onProgress(e.loaded);
    x.onload = () => {
      let d = {}; try { d = JSON.parse(x.responseText); } catch { /* not JSON */ }
      x.status >= 200 && x.status < 300 ? resolve(d) : reject(Object.assign(new Error(d.error || `Upload failed (${x.status})`), { status: x.status }));
    };
    x.onerror = () => reject(new Error('Connection lost'));
    x.ontimeout = () => reject(new Error('Upload timed out'));
    x.timeout = 10 * 60 * 1000;
    x.send(blob);
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function submitContribution() {
  if (C.busy) return;
  showErr('');
  const files = C.files.filter(f => !f.err);
  const links = $('#links').value.split(/\s+/).map(s => s.trim()).filter(Boolean);
  const context = $('#context').value.trim();
  if (C.files.some(f => f.err)) return showErr('Remove the files marked in red first.');
  if (!files.length && !links.length) return showErr('Add at least one file, folder or link.');
  if (files.length > C.MAX_FILES) return showErr(`Up to ${C.MAX_FILES} files at a time.`);
  const badLink = links.find(l => !/^https:\/\/[^/\s]+\.[^/\s]+/.test(l));
  if (badLink) return showErr(`“${badLink.slice(0, 60)}” isn’t a full https:// link.`);
  if (!$('#consent').checked) return showErr('Please tick the box confirming you can share these files.');
  if (S.turnstile && !C.tsToken) return showErr('Please wait for the human check to finish (just above the button).');

  C.busy = true;
  const btn = $('#csubmit'); btn.disabled = true; btn.firstChild.textContent = 'Sending… ';
  const leaveGuard = e => { e.preventDefault(); e.returnValue = ''; };
  window.addEventListener('beforeunload', leaveGuard);
  try {
    const sub = await api('/api/submissions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        context, links, credit: $('#credit').value.trim(), course_hint: $('#course').value.trim(),
        consent: true, turnstile: C.tsToken, files: files.map(f => ({ name: fname(f), size: f.size })),
      }),
    });
    remember(sub.id);
    $('#cform').hidden = true;
    const prog = $('#cprog'); prog.hidden = false;
    const total = files.reduce((a, f) => a + f.size, 0) || 1;
    let doneBytes = 0;
    prog.innerHTML = `<div class="cp-head"><div class="eyebrow">Uploading · code <b>${esc(sub.id)}</b></div><div class="cp-pct" id="cpPct">0%</div></div>
      <div class="bar-track"><div class="bar-fill" id="cpBar"></div></div>
      <div class="files">${files.map((f, i) => `<div class="frow" id="cpf${i}"><span class="ext k-${kind(f.name)}">${esc(ext(f.name).slice(0, 5))}</span><span class="fname">${esc(fname(f))}<small id="cps${i}">waiting</small></span><span class="fsize">${fmtSize(f.size)}</span><span></span></div>`).join('')}</div>
      <p class="fine">Keep this page open until the upload finishes.</p>`;
    const paint = extra => { const pct = Math.min(100, Math.floor((doneBytes + extra) / total * 100)); const b = $('#cpBar'), t = $('#cpPct'); if (b) b.style.width = pct + '%'; if (t) t.textContent = pct + '%'; };

    for (const [i, f] of files.entries()) {
      const plan = sub.files[i];
      const say = t => { const el = $('#cps' + i); if (el) el.textContent = t; };
      const sendPart = async n => {
        const start = (n - 1) * plan.part_size, blob = f.slice(start, Math.min(f.size, start + plan.part_size));
        for (let attempt = 1; ; attempt++) {
          try {
            await putPart(`${S.api}/api/submissions/${sub.id}/files/${i}/parts/${n}`, sub.token, blob, loaded => paint(loaded));
            doneBytes += blob.size; paint(0); return;
          } catch (e) {
            if ((e.status && e.status < 500 && e.status !== 429) || attempt >= 6) throw e;
            say(`connection problem, retrying (${attempt}/5)…`);
            await sleep(Math.min(30000, 1500 * 2 ** attempt));
          }
        }
      };
      say('uploading');
      for (let n = 1; n <= plan.parts; n++) await sendPart(n);
      for (let k = 0; k < 3; k++) {
        const r = await api(`/api/submissions/${sub.id}/files/${i}/complete`, { method: 'POST', headers: { Authorization: 'Bearer ' + sub.token } });
        if (r.ok) break;
        for (const n of r.missing || []) await sendPart(n);
      }
      say('uploaded ✓');
      $('#cpf' + i)?.classList.add('done');
    }
    await api(`/api/submissions/${sub.id}/finish`, { method: 'POST', headers: { Authorization: 'Bearer ' + sub.token } });
    window.removeEventListener('beforeunload', leaveGuard);
    C.busy = false;
    nav('status/' + sub.id);
  } catch (e) {
    window.removeEventListener('beforeunload', leaveGuard);
    C.busy = false;
    $('#cform').hidden = false; $('#cprog').hidden = true;
    btn.disabled = false; btn.firstChild.textContent = 'Send contribution ';
    if (window.turnstile && C.tsWidget !== null) { try { window.turnstile.reset(C.tsWidget); } catch { /* ignore */ } C.tsToken = ''; }
    showErr(e.message || 'Something went wrong. Please try again.');
  }
}

async function statusView(id) {
  const key = 'status/' + id;
  if (S.rendered !== key) {
    pageShell(key, 'Contribution ' + id, `<header class="page-head"><div class="eyebrow">Contribution</div><h1 class="mono-h">${esc(id)}</h1></header><div id="stbody"><div class="loading"><span class="spinner"></span> Checking status…</div></div>`);
  }
  clearInterval(C.pollTimer);
  if (!S.api) { $('#stbody').innerHTML = `<div class="notice">Status is available when the site is online.</div>`; return; }
  const load = async () => {
    if (S.rendered !== key) { clearInterval(C.pollTimer); return; }
    let s;
    try { s = await api(`/api/submissions/${encodeURIComponent(id)}`); } catch (e) {
      if (C.shown === key && e.status !== 404) return;  // a blip while polling shouldn't wipe a status already on screen
      $('#stbody').innerHTML = `<div class="notice">${esc(e.status === 404 ? 'No contribution with that code.' : e.message)}</div>`; return;
    }
    if (S.rendered !== key) return;
    remember(id); C.shown = key;
    const off = ['needs_info', 'rejected', 'abandoned'].includes(s.status);
    const idxNow = STAGES.findIndex(x => x[0] === s.status);
    const stage = s.status === 'uploading' ? -1 : idxNow;
    const offMsg = { needs_info: 'We need a bit more information. See the note below.', rejected: 'These files couldn\'t be added. See the note below.', abandoned: 'The upload wasn\'t finished, so it was cleared. Please send it again.' }[s.status];
    $('#stbody').innerHTML = `
      <ol class="stages">${STAGES.map(([k, name, desc], i) => `<li class="${i < stage ? 'past' : i === stage ? 'now' : ''}"><span class="dot"></span><div><b>${name}</b>${i === stage ? `<p>${desc}</p>` : ''}</div></li>`).join('')}</ol>
      ${off ? `<div class="notice warn">${esc(offMsg)}</div>` : ''}
      ${s.note ? `<div class="stnote"><span class="eyebrow">Note</span><p>${esc(s.note)}</p></div>` : ''}
      ${s.published?.length ? `<h2 class="label">Live now</h2><div class="files">${s.published.map(p => { const f = S.files.get(p); return `<a class="file" href="${href(p)}"><span class="ext k-${kind(p)}">${esc(ext(p))}</span><span class="fname">${esc(prettyFile(p.split('/').pop()))}<small>${esc(p.split('/').slice(0, -1).map(label).slice(-2).join(' › '))}</small></span><span class="fsize">${f ? fmtSize(f.s) : ''}</span><span class="fdate">open →</span></a>`; }).join('')}</div>` : ''}
      <h2 class="label">Sent</h2>
      <div class="files">${s.files.map(f => `<div class="frow"><span class="ext k-${kind(f.name)}">${esc(ext(f.name).slice(0, 5))}</span><span class="fname">${esc(f.name)}</span><span class="fsize">${fmtSize(f.size)}</span><span></span></div>`).join('')}${s.links ? `<div class="frow"><span class="ext k-dir">LINK</span><span class="fname">${plural(s.links, 'link')}</span><span></span><span></span></div>` : ''}</div>
      <div class="meta stmeta"><span>sent ${new Date(s.created).toLocaleString('en-GB')}</span>${s.issue_url ? `<a href="${esc(s.issue_url)}" target="_blank" rel="noopener">discussion ↗</a>` : ''}${s.pr_url ? `<a href="${esc(s.pr_url)}" target="_blank" rel="noopener">review ↗</a>` : ''}</div>
      <p class="fine">Bookmark this page or keep the code <b>${esc(id)}</b> to check back later.</p>`;
    if (['published', 'rejected', 'abandoned'].includes(s.status)) clearInterval(C.pollTimer);
  };
  await load();
  C.pollTimer = setInterval(load, 20000);
}

/* ── wiring ─────────────────────────────────────────── */
const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
function wire() {
  $('#kbdHint').textContent = isMac ? '⌘K' : 'Ctrl K';
  $('#searchBtn').addEventListener('click', openPalette);
  $('#closeBtn').addEventListener('click', () => {
    if (!S.preview) return;
    if (!EMBED && S.pushed) { S.pushed = false; history.back(); return; }  // back to the list exactly as it was, no history trap
    nav(S.preview.parent.p, true);
  });
  if (EMBED) {
    document.addEventListener('click', e => {
      const a = e.target.closest('a[href^="#/"]');
      if (!a || e.metaKey || e.ctrlKey) return;
      e.preventDefault();
      let p = a.getAttribute('href').slice(2);
      p = p.split('/').map(decodeURIComponent).join('/');
      nav(p);
    });
    $('#backBtn')?.addEventListener('click', goBack);
  }
  $('#scrim').addEventListener('click', () => $('#closeBtn').click());
  $('#prevBtn').addEventListener('click', () => step(-1));
  $('#nextBtn').addEventListener('click', () => step(1));
  $('#palette').addEventListener('mousedown', e => { if (e.target.id === 'palette') closePalette(); });
  let qTimer;
  $('#q').addEventListener('input', () => { clearTimeout(qTimer); qTimer = setTimeout(renderResults, 60); });
  $('#results').addEventListener('mousemove', e => { const r = e.target.closest('.res'); if (r && +r.dataset.i !== active) setActive(+r.dataset.i); });
  $('#results').addEventListener('click', e => { const r = e.target.closest('.res'); if (r) choose(+r.dataset.i); });
  document.addEventListener('keydown', e => {
    const pal = !$('#palette').hidden;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); pal ? closePalette() : openPalette(); return; }
    if (pal) {
      if (e.key === 'Escape') closePalette();
      else if (e.key === 'ArrowDown') { e.preventDefault(); setActive(active + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); }
      else if (e.key === 'Enter') { e.preventDefault(); choose(active); }
      return;
    }
    if (/^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName)) return;
    if (e.key === '/') { e.preventDefault(); openPalette(); }
    else if (S.preview && e.key === 'Escape') $('#closeBtn').click();
    else if (S.preview && e.key === 'ArrowLeft') step(-1);
    else if (S.preview && e.key === 'ArrowRight') step(1);
  });
  if (!EMBED) window.addEventListener('hashchange', route);
}

async function boot() {
  wire();
  try {
    let idx;
    if (EMBED) idx = JSON.parse(document.getElementById('sej-data').textContent);
    else {
      const r = await fetch(FAST + 'index.json', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      idx = await r.json();
    }
    S.base = idx.fileBase === null ? null : (idx.fileBase || 'files/');
    S.api = idx.api || null;
    S.turnstile = idx.turnstile || null;
    S.generated = idx.generated;
    S.root = idx.root;
    S.pg = idx.programmes || null;
    S.sources = idx.sources || [];
    if (S.pg) Object.assign(NAMES, S.pg.labels || {}, { [S.pg.umbrella]: 'B.Tech & B.E.' });
    prep(S.root, '', null);
  } catch (e) {
    const local = /^(localhost|127\.|\[::1\]|)$/.test(location.hostname);
    $('#view').innerHTML = `<div class="loading">Couldn’t load the archive index (${esc(e.message)}).<br><br>${local ? 'Start the site with <code>python3 serve.py</code> from the repository folder.' : 'Please check your connection and <a href="" onclick="location.reload();return false">reload</a>.'}</div>`;
    return;
  }
  let nFiles = 0, nLinks = 0;
  S.files.forEach(f => { if (f.k === 'link') nLinks++; else nFiles++; });
  $('#footStatus').textContent = `${fmtNum(nFiles)} files · ${fmtNum(nLinks)} links`;
  $('#footIndexed').textContent = `Updated ${new Date(S.generated).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}`;
  showVisitors();
  route();
}
boot();
