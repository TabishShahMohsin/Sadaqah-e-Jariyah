// End-to-end test of the intake Worker with local R2 + D1 (Miniflare).
// Turnstile and GitHub are mocked. Run:  npm test
import { Miniflare } from 'miniflare';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

const dir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const issues = [];
const mf = new Miniflare({
  modules: true,
  scriptPath: path.join(dir, 'src/index.js'),
  compatibilityDate: '2025-06-01',
  r2Buckets: ['INBOX'],
  d1Databases: ['DB'],
  bindings: {
    ALLOWED_ORIGINS: 'https://sej.example', GITHUB_REPO: 'me/sej',
    TURNSTILE_SECRET: 'test', GITHUB_TOKEN: 'gh', ADMIN_TOKEN: 'admin-secret-123', PUBLIC_REPO: 'true',
    LIMIT_PER_HOUR: '3',
  },
  outboundService: async (req) => {
    const u = new URL(req.url);
    if (u.hostname === 'challenges.cloudflare.com') {
      const fd = await req.formData();
      return Response.json({ success: fd.get('response') === 'good' });
    }
    if (u.hostname === 'api.github.com') {
      const body = await req.json();
      issues.push(body);
      return Response.json({ number: 40 + issues.length }, { status: 201 });
    }
    return new Response('blocked', { status: 599 });
  },
});

const db = await mf.getD1Database('DB');
for (const stmt of readFileSync(path.join(dir, 'schema.sql'), 'utf8').replace(/--.*$/gm, '').split(';').map(s => s.trim()).filter(Boolean)) {
  await db.prepare(stmt).run();
}
const base = 'https://intake.example';
const call = (p, init = {}) => mf.dispatchFetch(base + p, { ...init, headers: { Origin: 'https://sej.example', ...(init.headers || {}) } });
const post = (p, body, token) => call(p, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) } });

let pass = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); pass++; console.log('  ✓', msg); };

// a fake PDF of 45 MB → 3 parts of 20 MB
const pdf = new Uint8Array(45 * 1024 * 1024); pdf.set(new TextEncoder().encode('%PDF-1.7\n'));
const txt = new TextEncoder().encode('#include <stdio.h>\nint main(){return 0;}\n');
const fakePng = new TextEncoder().encode('MZ this is really an exe');

// 1. validation
let r = await post('/api/submissions', { context: 'x', consent: true, turnstile: 'good' });
ok(r.status === 400, 'needs at least one file or link');
r = await post('/api/submissions', { context: 'Mid sem paper DBMS 2024', files: [{ name: 'evil.exe', size: 10 }], consent: true, turnstile: 'good' });
ok(r.status === 400 && (await r.json()).error.includes('.exe'), 'rejects disallowed file type');
r = await post('/api/submissions', { context: 'Mid sem paper DBMS 2024', links: ['http://192.168.1.1/x'], consent: true, turnstile: 'good' });
ok(r.status === 400, 'rejects links that are not public https pages');
r = await post('/api/submissions', { context: 'Mid sem paper DBMS 2024', files: [{ name: 'a.pdf', size: 10 }], consent: true, turnstile: 'bad' });
ok(r.status === 400 && (await r.json()).error.includes('human check'), 'rejects failed Turnstile');
r = await post('/api/submissions', { context: 'Mid sem paper DBMS 2024', files: [{ name: 'a.pdf', size: 10 }], turnstile: 'good' });
ok(r.status === 400, 'requires consent');
ok(r.headers.get('Access-Control-Allow-Origin') === 'https://sej.example', 'CORS allows the site origin');
r = await call('/api/health', { headers: { Origin: 'https://evil.example' } });
ok(!r.headers.get('Access-Control-Allow-Origin'), 'CORS refuses other origins');

// 2. happy path
r = await post('/api/submissions', {
  context: 'DBMS mid-sem 2024-25 with solutions, Prof. X',
  credit: 'Ali', course_hint: 'Computer-Engineering/Sem3/COC2122-AIC2122_Database-Management-System',
  files: [{ name: 'DBMS Mid Sem.pdf', size: pdf.length }, { name: 'prog.c', size: txt.length }],
  links: ['https://drive.google.com/drive/folders/abc123'],
  consent: true, turnstile: 'good',
});
ok(r.status === 201, 'creates a submission');
const s = await r.json();
ok(/^[a-z0-9]{12}$/.test(s.id) && s.token.length > 30 && s.files[0].parts === 3, 'returns id, token, part plan');

const put = (i, n, body, token = s.token) => call(`/api/submissions/${s.id}/files/${i}/parts/${n}`, { method: 'PUT', body, headers: { Authorization: `Bearer ${token}` } });
const P = 20 * 1024 * 1024;
r = await put(0, 1, pdf.subarray(0, P), 'wrong-token');
ok(r.status === 403, 'rejects wrong upload token');
r = await put(0, 2, pdf.subarray(0, P - 5));
ok(r.status === 400, 'rejects a part of the wrong size');
r = await put(0, 1, pdf.subarray(0, P)); ok(r.status === 200, 'uploads part 1');
r = await post(`/api/submissions/${s.id}/files/0/complete`, {}, s.token);
const partial = await r.json();
ok(partial.ok === false && partial.missing.join() === '2,3', 'complete reports missing parts (resumable)');
r = await put(0, 3, pdf.subarray(2 * P)); ok(r.status === 200, 'uploads last part out of order');
r = await put(0, 2, pdf.subarray(P, 2 * P)); ok(r.status === 200, 'uploads part 2');
r = await put(0, 2, pdf.subarray(P, 2 * P)); ok(r.status === 200, 'retrying a part is harmless');
r = await post(`/api/submissions/${s.id}/finish`, {}, s.token);
ok(r.status === 409, 'cannot finish before every file is complete');
r = await post(`/api/submissions/${s.id}/files/0/complete`, {}, s.token);
ok((await r.json()).ok === true, 'completes the 45 MB file');
r = await put(1, 1, txt); ok(r.status === 200, 'uploads the code file');
r = await post(`/api/submissions/${s.id}/files/1/complete`, {}, s.token); ok((await r.json()).ok, 'completes the code file');

const inbox = await mf.getR2Bucket('INBOX');
const obj = await inbox.head(`inbox/${s.id}/0000-DBMS-Mid-Sem.pdf`);
ok(obj && obj.size === pdf.length, 'object is in the private inbox with the right size and a safe name');

r = await post(`/api/submissions/${s.id}/finish`, {}, s.token);
const fin = await r.json();
ok(r.status === 200 && fin.issue === 41, 'finish opens a GitHub issue');
const issue = issues.at(-1);
ok(issue.labels.includes('submission') && issue.body.includes(`sej-submission:${s.id}:`), 'issue is labelled and signed');
r = await put(1, 1, txt); ok(r.status === 409, 'no uploads after finishing');

// 3. issue body can't be broken out of
r = await post('/api/submissions', { context: 'notes ~~~\n@everyone [x](javascript:alert(1)) ~~~~~ end', links: ['https://drive.google.com/file/d/xyz/view'], consent: true, turnstile: 'good' });
const s2 = await r.json();
ok(r.status === 201 && s2.issue === 42, 'links-only submission opens an issue at once');
ok(issues.at(-1).body.includes('~~~~~~text') && !issues.at(-1).title.includes('@'), 'contributor text is fenced; title has no mentions');

// 4. magic bytes
r = await post('/api/submissions', { context: 'a png screenshot of notes', files: [{ name: 'notes.png', size: fakePng.length }], consent: true, turnstile: 'good' });
const s3 = await r.json();
r = await call(`/api/submissions/${s3.id}/files/0/parts/1`, { method: 'PUT', body: fakePng, headers: { Authorization: `Bearer ${s3.token}` } });
ok(r.status === 400 && (await r.json()).error.includes('real .png'), 'rejects a file whose content does not match its type');

// 5. rate limit (3/hour in this test config; 3 created above)
r = await post('/api/submissions', { context: 'one more set of notes', links: ['https://drive.google.com/file/d/q/view'], consent: true, turnstile: 'good' });
ok(r.status === 429, 'rate-limits by IP');

// 6. status + admin
r = await call(`/api/submissions/${s.id}`);
const st = await r.json();
ok(st.status === 'received' && st.files.length === 2 && st.issue_url.endsWith('/issues/41') && !('token_hash' in st) && !('context' in st), 'public status hides private fields');
r = await call(`/api/admin/submissions/${s.id}`);
ok(r.status === 403, 'admin endpoint needs the admin token');
r = await call(`/api/admin/submissions/${s.id}`, { headers: { Authorization: 'Bearer admin-secret-123' } });
const adm = await r.json();
ok(adm.context.startsWith('DBMS') && adm.files[0].r2_key.startsWith(`inbox/${s.id}/`) && !adm.token_hash, 'admin read returns the full record');
r = await call(`/api/admin/submissions/${s.id}`, { method: 'POST', body: JSON.stringify({ status: 'in_review', pr: 7, note: 'Sorted 2 files' }), headers: { Authorization: 'Bearer admin-secret-123' } });
r = await call(`/api/submissions/${s.id}`);
const st2 = await r.json();
ok(st2.status === 'in_review' && st2.pr_url.endsWith('/pull/7'), 'status updates reach the contributor');
const A = { headers: { Authorization: 'Bearer admin-secret-123' } };
r = await call('/api/admin/submissions?status=in_review', A);
ok((await r.json()).submissions.some(x => x.id === s.id), 'admin lists submissions by status');
r = await call('/api/admin/submissions?status=in_review');
ok(r.status === 403, 'listing needs the admin token');
r = await call(`/api/admin/submissions/${s.id}/files/1`, A);
ok(r.status === 200 && (await r.text()).includes('int main'), 'admin downloads an uploaded file');
r = await call(`/api/admin/submissions/${s.id}/purge`, { method: 'POST', ...A });
ok((await r.json()).purged === 2 && !(await inbox.head(`inbox/${s.id}/0000-DBMS-Mid-Sem.pdf`)), 'purge empties the inbox for a sorted submission');
r = await call('/api/submissions/../../admin');
ok(r.status === 404, 'odd paths are 404');

await mf.dispose();
console.log(`\n${pass} checks passed`);
