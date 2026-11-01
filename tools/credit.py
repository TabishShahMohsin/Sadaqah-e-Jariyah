#!/usr/bin/env python3
"""
Credits: who shared a file with the archive, and who made or taught it — kept in the catalog, not in file names.

Every file can carry, in its catalog metadata:
    credit     who shared it with the archive: a contributor's chosen name (e.g. gk7093) or a source
               (AMU Cloud, AMU Drive, Engineering College Library, AMU website). Several are joined with ", ".
    professor  who taught it          author  who wrote / made it          year  YYYY-YY
The site shows them when a file is opened ("notes by … · taught by … · shared by …").

    python3 tools/credit.py report                         who is credited for how many files
    python3 tools/credit.py set --credit NAME PATH…        add a credit (also --professor, --author, --year)
    python3 tools/credit.py change CHANGE-ID --credit NAME credit every file a catalog change set added
    python3 tools/credit.py tags [--apply]                 source tags in names (…_FROM_AMU_Cloud, …_AMU-Drive) → credit + clean name
    python3 tools/credit.py teachers [--apply]             teacher prefixes (Ishtiyak-Sir_…, Dr-Inamullah_…) → professor + clean name

Every change is written as one catalog change set (meta + move ops), so it is reviewable and replayable.
"""
import argparse
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rules  # noqa: E402
import sej  # noqa: E402

# name tag → (credit, pattern to strip)
TAGS = [
    ('AMU Cloud', re.compile(r'[_\s-]*FROM[_\s-]AMU[_\s-]Cloud', re.I)),
    ('AMU Cloud', re.compile(r'(?:^AMU-Cloud[_-]|[_-]AMU-Cloud(?=[._]))', re.I)),
    ('AMU Drive', re.compile(r'[_-]AMU-Drive(?=[._])', re.I)),
    ('PDF Drive', re.compile(r'\s*\(\s*PDFDrive\s*\)', re.I)),
]
TEACHER = re.compile(r"^(?:Dr[-.\s]?(?P<dr>[A-Z][a-z]+)|(?P<sir>[A-Z][a-z]+)[-\s]Sir)[_\s-]+")
TEACHER_INNER = re.compile(r"\s(?P<sir>[A-Z][a-z]+)\s+Sir'?s?\b")


def merged(old, new):
    names = [x.strip() for x in (old or '').split(',') if x.strip()]
    for n in [x.strip() for x in new.split(',') if x.strip()]:
        if n not in names:
            names.append(n)
    return ', '.join(names)


def meta_op(state, p, credit=None, **kw):
    cur = state[p].get('meta') or {}
    m = {k: v for k, v in kw.items() if v}
    if credit:
        m['credit'] = merged(cur.get('credit'), credit)
    m = {k: v for k, v in m.items() if cur.get(k) != v}
    return {'op': 'meta', 'p': p, 'meta': m} if m else None


def tidy(name):
    stem, dot, ext = name.rpartition('.')
    stem = re.sub(r'[_\s-]{2,}', lambda m: '_' if '_' in m.group(0) else m.group(0)[0], stem).strip('_- ')
    return f'{stem}.{ext}' if dot else name


def renames(state, fn):
    """fn(name) -> (new_name, meta dict) or None. Returns ops; renamed files keep their hash-cache entries."""
    ops, taken = [], set(state)
    for p in sorted(state):
        d, name = p.rsplit('/', 1)
        r = fn(name)
        if not r:
            continue
        new, meta = r
        new = tidy(new)
        dst = p if new == name else rules.unique_path(f'{d}/{new}', taken - {p})
        if dst != p:
            taken.discard(p)
            taken.add(dst)
            ops.append({'op': 'move', 'from': p, 'to': dst})
        state_p = p
        op = meta_op(state, state_p, **meta)
        if op:
            op['p'] = dst
            ops.append(op)
    return ops


def apply_ops(ops, note, apply):
    moves = [o for o in ops if o['op'] == 'move']
    metas = [o for o in ops if o['op'] == 'meta']
    for o in moves[:12]:
        print(f"  {o['from'].rsplit('/', 1)[-1][:60]:60} → {o['to'].rsplit('/', 1)[-1]}")
    print(f'{len(moves)} renames, {len(metas)} credit updates' + ('' if apply else '  (dry run; add --apply)'))
    if not apply or not ops:
        return
    cache = sej.HashCache()
    for o in moves:
        src, dst = os.path.join(REPO, o['from']), os.path.join(REPO, o['to'])
        if os.path.exists(dst):
            sys.exit(f'refusing to overwrite {o["to"]}')
        tmp = src + '.renaming'
        os.rename(src, tmp)  # two steps: a case-only rename is safe on case-insensitive disks
        os.rename(tmp, dst)
        hit = cache.d.pop(o['from'], None)
        if hit:
            cache.d[o['to']] = hit
    cache.save()
    print('→', os.path.relpath(sej.write_change(ops, {'kind': 'local', 'by': 'maintainer', 'note': note}, 'credits'), REPO))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('report')
    s = sub.add_parser('set')
    s.add_argument('paths', nargs='+')
    c = sub.add_parser('change')
    c.add_argument('change_id')
    for x in (s, c):
        x.add_argument('--credit')
        x.add_argument('--professor')
        x.add_argument('--author')
        x.add_argument('--year')
        x.add_argument('--apply', action='store_true')
    for name in ('tags', 'teachers'):
        sub.add_parser(name).add_argument('--apply', action='store_true')
    a = ap.parse_args()
    state = sej.load_state()

    if a.cmd == 'report':
        cnt = collections.Counter()
        for e in state.values():
            for n in [x.strip() for x in ((e.get('meta') or {}).get('credit') or '').split(',') if x.strip()] or ['(no credit yet)']:
                cnt[n] += 1
        for n, k in cnt.most_common():
            print(f'{k:6}  {n}')
        return

    if a.cmd in ('set', 'change'):
        paths = [os.path.relpath(os.path.abspath(p), REPO) for p in a.paths] if a.cmd == 'set' else \
            [p for p, e in state.items() if e.get('change') == a.change_id]
        missing = [p for p in paths if p not in state]
        if missing:
            sys.exit(f'not in the catalog: {missing[:3]}')
        ops = [o for o in (meta_op(state, p, a.credit, professor=a.professor, author=a.author, year=a.year) for p in paths) if o]
        apply_ops(ops, f'credit: {a.credit or a.professor or a.author}', a.apply)
        return

    if a.cmd == 'tags':
        def fn(name):
            for credit, rx in TAGS:
                if rx.search(name):
                    return rx.sub('', name), {'credit': credit}
        apply_ops(renames(state, fn), 'source tags in file names → credits', a.apply)
        return

    if a.cmd == 'teachers':
        def fn(name):
            m = TEACHER.match(name)
            if m:
                prof = f"Dr. {m.group('dr')}" if m.group('dr') else m.group('sir')
                return name[m.end():] or name, {'professor': prof}
            m = TEACHER_INNER.search(name)
            if m:
                return TEACHER_INNER.sub('', name), {'professor': m.group('sir')}
        apply_ops(renames(state, fn), 'teacher names in file names → professor', a.apply)


if __name__ == '__main__':
    sys.exit(main())
