/**
 * Sadaqah-e-Jariyah — contribution intake.
 *
 * Contributors' files land in a PRIVATE bucket (INBOX). Nothing here makes anything public:
 * publishing happens only after a maintainer approves the pull request Claude prepares.
 *
 * Public:
 *   POST /api/submissions                          start: context, links, file list, Turnstile token → id + upload token
 *   PUT  /api/submissions/:id/files/:i/parts/:n    upload one part (Bearer upload token)
 *   POST /api/submissions/:id/files/:i/complete    finish one file  (Bearer upload token)
 *   POST /api/submissions/:id/finish               all files done → GitHub issue (Bearer upload token)
 *   GET  /api/submissions/:id                      status for the contributor
 * Maintainer automation (Bearer ADMIN_TOKEN):
 *   GET  /api/admin/submissions/:id                full record for the sorting job
 *   POST /api/admin/submissions/:id                update status / note / pr / published
 */

const PART_SIZE = 20 * 1024 * 1024; // R2 needs equal-sized parts (except the last); 20 MB stays under request limits
const MAX_FILE_BYTES = 500 * 1024 * 1024;
const MAX_FILES = 20;
const MAX_LINKS = 10;
const ALLOWED_EXT = new Set(['pdf', 'png', 'jpg', 'jpeg', 'webp', 'pptx', 'ppt', 'docx', 'doc', 'xlsx', 'xls',
  'mp4', 'mov', 'webm', 'txt', 'md', 'c', 'cpp', 'h', 'hpp', 'java', 'py', 'ipynb', 'csv']);
const TEXT_EXT = new Set(['txt', 'md', 'c', 'cpp', 'h', 'hpp', 'java', 'py', 'ipynb', 'csv']);
const STATUSES = new Set(['uploading', 'received', 'sorting', 'in_review', 'published', 'needs_info', 'rejected', 'abandoned']);
const ID_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789';

class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

/* ── helpers ─────────────────────────────────────────── */
const now = () => Date.now();
const enc = new TextEncoder();

function randomId(len = 12) {
  const bytes = crypto.getRandomValues(new Uint8Array(len));
  return [...bytes].map(b => ID_ALPHABET[b % ID_ALPHABET.length]).join('');
}
function randomToken() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
async function sha256Hex(s) {
  const d = await crypto.subtle.digest('SHA-256', enc.encode(s));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join('');
}
async function hmacHex(secret, s) {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(s));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, '0')).join('');
}
function safeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}
const extOf = n => { const i = n.lastIndexOf('.'); return i > 0 ? n.slice(i + 1).toLowerCase() : ''; };
function safeName(name) {
  const base = String(name).replace(/\\/g, '/').split('/').pop();
  const i = base.lastIndexOf('.');
  let stem = i > 0 ? base.slice(0, i) : base;
  const ext = i > 0 ? base.slice(i + 1).toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 5) : '';
  stem = stem.replace(/[^A-Za-z0-9._()-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '').slice(0, 100) || 'file';
  return ext ? `${stem}.${ext}` : stem;
}
function cleanText(s, max) {
  if (typeof s !== 'string') return '';
  // drop control chars except newline/tab, normalise newlines
  return s.replace(/\r\n?/g, '\n').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F‪-‮⁦-⁩]/g, '').trim().slice(0, max);
}
function isDriveLink(u) {
  try {
    const url = new URL(u);
    return url.protocol === 'https:' && ['drive.google.com', 'docs.google.com'].includes(url.hostname) && u.length <= 500;
  } catch { return false; }
}
function magicOk(ext, b) {
  const starts = (...xs) => xs.every((x, i) => b[i] === x);
  const ascii = (off, s) => [...s].every((c, i) => b[off + i] === c.charCodeAt(0));
  switch (ext) {
    case 'pdf': { const head = new TextDecoder('latin1').decode(b.subarray(0, 1024)); return head.includes('%PDF-'); }
    case 'png': return starts(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a);
    case 'jpg': case 'jpeg': return starts(0xff, 0xd8, 0xff);
    case 'webp': return ascii(0, 'RIFF') && ascii(8, 'WEBP');
    case 'pptx': case 'docx': case 'xlsx': return starts(0x50, 0x4b, 0x03, 0x04);
    case 'ppt': case 'doc': case 'xls': return starts(0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1);
    case 'mp4': case 'mov': return ['ftyp', 'moov', 'wide', 'mdat', 'free'].some(s => ascii(4, s));
    case 'webm': return starts(0x1a, 0x45, 0xdf, 0xa3);
  }
  if (TEXT_EXT.has(ext)) {
    const sample = b.subarray(0, 65536);
    if (sample.includes(0)) return false;
    try { new TextDecoder('utf-8', { fatal: true }).decode(sample.subarray(0, Math.max(0, sample.length - 4))); return true; } catch { return false; }
  }
  return false;
}

/* ── HTTP plumbing ───────────────────────────────────── */
function corsHeaders(req, env) {
  const origin = req.headers.get('Origin') || '';
  const allowed = (env.ALLOWED_ORIGINS || '').split(',').map(s => s.trim()).filter(Boolean);
  const h = {
    'Vary': 'Origin',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Max-Age': '86400',
  };
  if (allowed.includes(origin)) h['Access-Control-Allow-Origin'] = origin;
  return h;
}
function json(data, status, cors) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store', ...cors },
  });
}
async function readJson(req, limit = 64 * 1024) {
  const len = +req.headers.get('Content-Length') || 0;
  if (len > limit) throw new HttpError(413, 'Request is too large.');
  const text = await req.text();
  if (text.length > limit) throw new HttpError(413, 'Request is too large.');
  try { return JSON.parse(text); } catch { throw new HttpError(400, 'Request body must be JSON.'); }
}
function bearer(req) {
  const h = req.headers.get('Authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

/* ── data access ─────────────────────────────────────── */
async function getSubmission(env, id) {
  if (!/^[a-z0-9]{12}$/.test(id)) throw new HttpError(404, 'No contribution with that code.');
  const s = await env.DB.prepare('SELECT * FROM submissions WHERE id = ?').bind(id).first();
  if (!s) throw new HttpError(404, 'No contribution with that code.');
  return s;
}
async function requireUploader(req, env, id) {
  const s = await getSubmission(env, id);
  const token = bearer(req);
  if (!token || !safeEqual(await sha256Hex(token), s.token_hash)) throw new HttpError(403, 'This upload link has expired. Start the contribution again.');
  return s;
}
function requireAdmin(req, env) {
  if (!env.ADMIN_TOKEN || !safeEqual(bearer(req), env.ADMIN_TOKEN)) throw new HttpError(403, 'Forbidden.');
}
async function getFile(env, id, idx) {
  const f = await env.DB.prepare('SELECT * FROM files WHERE submission_id = ? AND idx = ?').bind(id, idx).first();
  if (!f) throw new HttpError(404, 'Unknown file.');
  return f;
}

/* ── Turnstile & limits ──────────────────────────────── */
async function verifyTurnstile(env, token, ip) {
  if (!env.TURNSTILE_SECRET) throw new HttpError(500, 'Contributions are not configured yet.');
  if (typeof token !== 'string' || !token || token.length > 2048) throw new HttpError(400, 'Please complete the human check.');
  const body = new FormData();
  body.append('secret', env.TURNSTILE_SECRET);
  body.append('response', token);
  if (ip) body.append('remoteip', ip);
  const r = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', { method: 'POST', body });
  const out = await r.json().catch(() => ({}));
  if (!out.success) throw new HttpError(400, 'The human check failed or expired. Please try it again.');
}
async function checkLimits(env, ipHash, bytes) {
  const hour = now() - 3600e3, day = now() - 86400e3;
  const perHour = +(env.LIMIT_PER_HOUR || 6), perDay = +(env.LIMIT_PER_DAY || 20);
  const globalDay = +(env.LIMIT_GLOBAL_PER_DAY || 300), globalBytes = +(env.LIMIT_GLOBAL_BYTES_PER_DAY || 40e9);
  const mine = await env.DB.prepare('SELECT SUM(created > ?) AS h, COUNT(*) AS d FROM submissions WHERE ip_hash = ? AND created > ?').bind(hour, ipHash, day).first();
  if ((mine.h || 0) >= perHour) throw new HttpError(429, 'You have sent several contributions in the last hour. Please try again a little later.');
  if ((mine.d || 0) >= perDay) throw new HttpError(429, 'Daily contribution limit reached. Please try again tomorrow.');
  const all = await env.DB.prepare('SELECT COUNT(*) AS n, COALESCE(SUM(total_bytes),0) AS b FROM submissions WHERE created > ?').bind(day).first();
  if (all.n >= globalDay || all.b + bytes > globalBytes) throw new HttpError(503, 'We are receiving a lot of uploads today. Please try again tomorrow.');
}

/* ── GitHub ──────────────────────────────────────────── */
function fence(text) {
  // a fence longer than any run of backticks/tildes inside, so contributor text can't break out of the block
  const longest = Math.max(2, ...[...text.matchAll(/~+/g)].map(m => m[0].length));
  return '~'.repeat(longest + 1);
}
async function createIssue(env, s) {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return null;
  const files = (await env.DB.prepare('SELECT idx, name, size, ext FROM files WHERE submission_id = ? ORDER BY idx').bind(s.id).all()).results;
  const links = JSON.parse(s.links || '[]');
  const titleBits = cleanText(s.context, 200).replace(/[\n`@#<>[\]]/g, ' ').replace(/\s+/g, ' ').slice(0, 70);
  const block = (t) => { const f = fence(t); return `${f}text\n${t}\n${f}`; };
  const sig = await hmacHex(env.ADMIN_TOKEN, s.id);
  const body = [
    `<!-- sej-submission:${s.id}:${sig} -->`,
    `New contribution **${s.id}**. Claude will sort it and open a pull request for review.`,
    '',
    '**What the contributor said** (untrusted text, shown as-is):',
    block(s.context),
    s.course_hint ? `**Course they picked:**\n${block(s.course_hint)}` : '',
    s.credit ? `**Credit as:**\n${block(s.credit)}` : '',
    files.length ? `**Files (${files.length}):**\n${block(files.map(f => `${f.idx}. ${f.name}  (${(f.size / 1e6).toFixed(1)} MB)`).join('\n'))}` : '',
    links.length ? `**Drive links:**\n${block(links.join('\n'))}` : '',
  ].filter(Boolean).join('\n');
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/issues`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${env.GITHUB_TOKEN}`,
      'Accept': 'application/vnd.github+json',
      'User-Agent': 'sej-intake',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    body: JSON.stringify({ title: `Contribution ${s.id}: ${titleBits || 'files'}`, body, labels: ['submission'] }),
  });
  if (!r.ok) { console.log('github issue failed', r.status, await r.text()); return null; }
  const out = await r.json();
  await env.DB.prepare('UPDATE submissions SET issue = ?, updated = ? WHERE id = ?').bind(out.number, now(), s.id).run();
  return out.number;
}

/* ── handlers ────────────────────────────────────────── */
async function createSubmission(req, env) {
  const ip = req.headers.get('CF-Connecting-IP') || '';
  const b = await readJson(req);
  const context = cleanText(b.context, 4000);
  const credit = cleanText(b.credit, 80) || null;
  const courseHint = cleanText(b.course_hint, 300) || null;
  const links = Array.isArray(b.links) ? [...new Set(b.links.map(l => cleanText(l, 500)).filter(Boolean))] : [];
  const files = Array.isArray(b.files) ? b.files : [];

  if (b.consent !== true) throw new HttpError(400, 'Please confirm you have the right to share these files.');
  if (context.length < 8) throw new HttpError(400, 'Tell us a little about the files: which course, what they are, which year.');
  if (!files.length && !links.length) throw new HttpError(400, 'Add at least one file or a Google Drive link.');
  if (files.length > MAX_FILES) throw new HttpError(400, `Up to ${MAX_FILES} files at a time.`);
  if (links.length > MAX_LINKS) throw new HttpError(400, `Up to ${MAX_LINKS} links at a time.`);
  const badLink = links.find(l => !isDriveLink(l));
  if (badLink) throw new HttpError(400, 'Only Google Drive links are accepted. For anything else, download the file and drop it here.');

  let total = 0;
  const clean = files.map((f, i) => {
    const name = cleanText(f && f.name, 255);
    const size = Number(f && f.size);
    const ext = extOf(name);
    if (!name) throw new HttpError(400, `File ${i + 1} has no name.`);
    if (!ALLOWED_EXT.has(ext)) throw new HttpError(400, `“${name}”: .${ext || '?'} files aren’t accepted.`);
    if (!Number.isInteger(size) || size <= 0) throw new HttpError(400, `“${name}” is empty.`);
    if (size > MAX_FILE_BYTES) throw new HttpError(400, `“${name}” is larger than 500 MB. Share it as a Google Drive link instead.`);
    total += size;
    return { name, size, ext };
  });

  await verifyTurnstile(env, b.turnstile, ip);
  const ipHash = await hmacHex(env.ADMIN_TOKEN || 'x', ip);
  await checkLimits(env, ipHash, total);

  const id = randomId();
  const token = randomToken();
  const t = now();
  const stmts = [env.DB.prepare(
    'INSERT INTO submissions (id, token_hash, ip_hash, created, updated, status, context, credit, course_hint, links, total_bytes) VALUES (?,?,?,?,?,?,?,?,?,?,?)'
  ).bind(id, await sha256Hex(token), ipHash, t, t, clean.length ? 'uploading' : 'received', context, credit, courseHint, JSON.stringify(links), total)];

  const out = [];
  for (const [i, f] of clean.entries()) {
    const key = `inbox/${id}/${String(i).padStart(2, '0')}-${safeName(f.name)}`;
    const mpu = await env.INBOX.createMultipartUpload(key, { customMetadata: { submission: id, original: f.name.slice(0, 200) } });
    const parts = Math.max(1, Math.ceil(f.size / PART_SIZE));
    stmts.push(env.DB.prepare('INSERT INTO files (submission_id, idx, name, size, ext, r2_key, upload_id, part_size, parts) VALUES (?,?,?,?,?,?,?,?,?)')
      .bind(id, i, f.name, f.size, f.ext, key, mpu.uploadId, PART_SIZE, parts));
    out.push({ idx: i, part_size: PART_SIZE, parts });
  }
  await env.DB.batch(stmts);

  let issue = null;
  if (!clean.length) issue = await createIssue(env, await getSubmission(env, id)); // links only: nothing to upload
  return { id, token, files: out, status: clean.length ? 'uploading' : 'received', issue };
}

async function uploadPart(req, env, id, idx, n) {
  const s = await requireUploader(req, env, id);
  if (s.status !== 'uploading') throw new HttpError(409, 'This contribution is already submitted.');
  const f = await getFile(env, id, idx);
  if (f.done) throw new HttpError(409, 'This file is already uploaded.');
  if (!Number.isInteger(n) || n < 1 || n > f.parts) throw new HttpError(400, 'Bad part number.');
  const expected = n < f.parts ? f.part_size : f.size - f.part_size * (f.parts - 1);
  const declared = +req.headers.get('Content-Length');
  if (declared && declared !== expected) throw new HttpError(400, 'Part size mismatch.');
  const buf = new Uint8Array(await req.arrayBuffer());
  if (buf.length !== expected) throw new HttpError(400, 'Part size mismatch. Please retry.');
  if (n === 1 && !magicOk(f.ext, buf)) {
    throw new HttpError(400, `“${f.name}” doesn’t look like a real .${f.ext} file.`);
  }
  const mpu = env.INBOX.resumeMultipartUpload(f.r2_key, f.upload_id);
  const part = await mpu.uploadPart(n, buf);
  await env.DB.prepare('INSERT OR REPLACE INTO parts (submission_id, idx, n, etag, size) VALUES (?,?,?,?,?)').bind(id, idx, n, part.etag, buf.length).run();
  return { ok: true };
}

async function completeFile(req, env, id, idx) {
  const s = await requireUploader(req, env, id);
  const f = await getFile(env, id, idx);
  if (f.done) return { ok: true };
  if (s.status !== 'uploading') throw new HttpError(409, 'This contribution is already submitted.');
  const rows = (await env.DB.prepare('SELECT n, etag, size FROM parts WHERE submission_id = ? AND idx = ? ORDER BY n').bind(id, idx).all()).results;
  const missing = [];
  for (let n = 1; n <= f.parts; n++) if (!rows.find(r => r.n === n)) missing.push(n);
  if (missing.length) return { ok: false, missing };
  if (rows.reduce((a, r) => a + r.size, 0) !== f.size) throw new HttpError(400, 'Uploaded size doesn’t match. Please retry this file.');
  const mpu = env.INBOX.resumeMultipartUpload(f.r2_key, f.upload_id);
  await mpu.complete(rows.map(r => ({ partNumber: r.n, etag: r.etag })));
  const head = await env.INBOX.head(f.r2_key);
  if (!head || head.size !== f.size) throw new HttpError(500, 'Upload could not be verified. Please retry.');
  await env.DB.prepare('UPDATE files SET done = 1 WHERE submission_id = ? AND idx = ?').bind(id, idx).run();
  return { ok: true };
}

async function finish(req, env, id) {
  const s = await requireUploader(req, env, id);
  if (s.status === 'uploading') {
    const left = await env.DB.prepare('SELECT COUNT(*) AS n FROM files WHERE submission_id = ? AND done = 0').bind(id).first();
    if (left.n) throw new HttpError(409, `${left.n} file(s) haven’t finished uploading.`);
    await env.DB.prepare("UPDATE submissions SET status = 'received', updated = ? WHERE id = ? AND status = 'uploading'").bind(now(), id).run();
  }
  const fresh = await getSubmission(env, id);
  const issue = fresh.issue || await createIssue(env, fresh);
  return { ok: true, id, status: 'received', issue };
}

async function publicStatus(env, id) {
  const s = await getSubmission(env, id);
  const files = (await env.DB.prepare('SELECT name, size, done FROM files WHERE submission_id = ? ORDER BY idx').bind(id).all()).results;
  const repo = env.GITHUB_REPO;
  return {
    id: s.id, status: s.status, created: s.created, updated: s.updated, note: s.note,
    files: files.map(f => ({ name: f.name, size: f.size, uploaded: !!f.done })),
    links: JSON.parse(s.links || '[]').length,
    issue_url: s.issue && repo ? `https://github.com/${repo}/issues/${s.issue}` : null,
    pr_url: s.pr && repo ? `https://github.com/${repo}/pull/${s.pr}` : null,
    published: JSON.parse(s.published || '[]'),
  };
}

async function adminGet(env, id) {
  const s = await getSubmission(env, id);
  const files = (await env.DB.prepare('SELECT idx, name, size, ext, r2_key, done FROM files WHERE submission_id = ? ORDER BY idx').bind(id).all()).results;
  const { token_hash, ip_hash, ...rest } = s;
  return { ...rest, links: JSON.parse(s.links || '[]'), published: JSON.parse(s.published || '[]'), files };
}

async function adminUpdate(req, env, id) {
  await getSubmission(env, id);
  const b = await readJson(req);
  const sets = [], vals = [];
  if (b.status !== undefined) { if (!STATUSES.has(b.status)) throw new HttpError(400, 'bad status'); sets.push('status = ?'); vals.push(b.status); }
  if (b.note !== undefined) { sets.push('note = ?'); vals.push(cleanText(String(b.note), 2000)); }
  if (b.pr !== undefined) { sets.push('pr = ?'); vals.push(Number.isInteger(b.pr) ? b.pr : null); }
  if (b.issue !== undefined) { sets.push('issue = ?'); vals.push(Number.isInteger(b.issue) ? b.issue : null); }
  if (Array.isArray(b.published)) { sets.push('published = ?'); vals.push(JSON.stringify(b.published.slice(0, 200).map(p => cleanText(String(p), 400)))); }
  if (!sets.length) return { ok: true };
  sets.push('updated = ?'); vals.push(now());
  await env.DB.prepare(`UPDATE submissions SET ${sets.join(', ')} WHERE id = ?`).bind(...vals, id).run();
  return { ok: true };
}

/* ── cron: tidy up ───────────────────────────────────── */
async function scheduled(env) {
  const stale = now() - 24 * 3600e3;
  const old = (await env.DB.prepare("SELECT id FROM submissions WHERE status = 'uploading' AND updated < ? LIMIT 50").bind(stale).all()).results;
  for (const { id } of old) {
    const files = (await env.DB.prepare('SELECT r2_key, upload_id, done FROM files WHERE submission_id = ?').bind(id).all()).results;
    for (const f of files) {
      try {
        if (f.done) await env.INBOX.delete(f.r2_key);
        else await env.INBOX.resumeMultipartUpload(f.r2_key, f.upload_id).abort();
      } catch (e) { console.log('cleanup', id, e && e.message); }
    }
    await env.DB.prepare("UPDATE submissions SET status = 'abandoned', updated = ? WHERE id = ?").bind(now(), id).run();
    await env.DB.prepare('DELETE FROM parts WHERE submission_id = ?').bind(id).run();
  }
  const orphans = (await env.DB.prepare("SELECT * FROM submissions WHERE status = 'received' AND issue IS NULL AND updated < ? LIMIT 20").bind(now() - 5 * 60e3).all()).results;
  for (const s of orphans) await createIssue(env, s);
}

/* ── router ──────────────────────────────────────────── */
export default {
  async fetch(req, env) {
    const cors = corsHeaders(req, env);
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    const url = new URL(req.url);
    const p = url.pathname.replace(/\/+$/, '');
    let m;
    try {
      if (p === '/api/health') return json({ ok: true }, 200, cors);
      if (p === '/api/submissions' && req.method === 'POST') return json(await createSubmission(req, env), 201, cors);
      if ((m = p.match(/^\/api\/submissions\/([a-z0-9]+)\/files\/(\d{1,2})\/parts\/(\d{1,3})$/)) && req.method === 'PUT')
        return json(await uploadPart(req, env, m[1], +m[2], +m[3]), 200, cors);
      if ((m = p.match(/^\/api\/submissions\/([a-z0-9]+)\/files\/(\d{1,2})\/complete$/)) && req.method === 'POST')
        return json(await completeFile(req, env, m[1], +m[2]), 200, cors);
      if ((m = p.match(/^\/api\/submissions\/([a-z0-9]+)\/finish$/)) && req.method === 'POST')
        return json(await finish(req, env, m[1]), 200, cors);
      if ((m = p.match(/^\/api\/submissions\/([a-z0-9]+)$/)) && req.method === 'GET')
        return json(await publicStatus(env, m[1]), 200, cors);
      if ((m = p.match(/^\/api\/admin\/submissions\/([a-z0-9]+)$/))) {
        requireAdmin(req, env);
        if (req.method === 'GET') return json(await adminGet(env, m[1]), 200, {});
        if (req.method === 'POST') return json(await adminUpdate(req, env, m[1]), 200, {});
      }
      return json({ error: 'Not found.' }, 404, cors);
    } catch (e) {
      if (e instanceof HttpError) return json({ error: e.message }, e.status, cors);
      console.log('error', e && e.stack);
      return json({ error: 'Something went wrong on our side. Please try again in a minute.' }, 500, cors);
    }
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(scheduled(env));
  },
};
