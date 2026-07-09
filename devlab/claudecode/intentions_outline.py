#!/usr/bin/env python3
"""
intentions_outline.py — every intention we have a record for, grouped by device.

Built 2026-07-09 for Akien's review pass. Three record types in devlab/runtime/memory/ carry an
intention and all three are collected here:

    intentions/     formal I-xxx intentions (the root artifacts of Intention-Based Development)
    architecture/   intention-points: a subsystem's intent plus the files that implement it
    tickets/        an 'I intend that...' statement on each non-terminal ticket

ATTRIBUTION IS THE HARD PART, and it is deliberately conservative. Several devices are named with
ordinary English words — queue, reader, critic, policy, intent, template, workspace, sensor,
minion, claude, postgres. Matching those against free-text titles would file half the backlog
under 'queue'. So they are matched ONLY on an exact tag or on the device name inside the ticket
id; only DISTINCTIVE names (granny, dicksimnel, librarian, ...) are matched in title text.

A ticket with no device evidence is NOT junk. Measured on first run: 106 of 189 open intentions
have no device, and they are tagged Consequence / Workflow / Proof / Skills / Audits — they are
intentions about the PROCESS, which has no device. Section 3 groups them by theme rather than
hiding them in an 'unattributed' bucket, because that ratio is itself the finding: the dev
process is the largest unowned subsystem in the project.

Output is a space-indented plain-text outline (no markdown), written to Akien's inbox.
Re-run it after any review pass; it reads the store and asserts nothing.
"""

import json, glob, os, textwrap, collections, datetime

TERMINAL = {'closed', 'cancelled', 'done', 'discarded'}
ROOT = 'devlab/runtime/memory'

# Devices whose names are DISTINCTIVE enough to match in free title text.
DISTINCTIVE = {
    'igor','granny','nanny','scraps','librarian','skeleton','dicksimnel','vault','auditor',
    'hubert','vetinari','archivist','ground_loop','inference','bus','classifier','installer',
    'discord_bot','calibre','swadl','aider','browser_use','google_secretary','sudo_relay',
    'build_digester','demo_daemon','rack_test','web_server','summarizer',
}
# Devices whose names are ordinary words — tag / id-prefix evidence ONLY, never free text.
GENERIC = {
    'akien','claude','critic','evaluator','improver','intent','minion','policy','ponder',
    'postgres','queue','reader','sensor','template','workspace','identities',
}
DEVICES = sorted(DISTINCTIVE | GENERIC)
ALIASES = {'ds':'dicksimnel','cc':'claude','gl':'ground_loop','groundloop':'ground_loop',
           'discord':'discord_bot','web':'web_server','dsimnel':'dicksimnel'}

def norm(s): return (s or '').lower().replace('-', '_')

def attribute(tid, title, tags):
    hits = set()
    ntags = {norm(t) for t in tags}
    for d in DEVICES:
        if d in ntags or ALIASES.get(d) in ntags:
            hits.add(d)
    for a, d in ALIASES.items():
        if a in ntags: hits.add(d)
    nid = norm(tid)
    for d in DEVICES:
        if nid.startswith(f't_{d}_') or f'_{d}_' in nid:
            hits.add(d)
    for a, d in ALIASES.items():
        if nid.startswith(f't_{a}_') or f'_{a}_' in nid:
            hits.add(d)
    ntitle = norm(title)
    for d in DISTINCTIVE:
        if d in ntitle: hits.add(d)
    return sorted(hits)

# Canonical dev-process themes, most-specific first. A ticket's theme is its FIRST matching tag
# in this order, so `Proof`+`Workflow` files under Proof rather than by tag-list accident. Case is
# normalised (Skills/skills) and near-synonyms merged (Process/DevProcess).
THEME_MAP = [
    ('consequence',   {'consequence'}),
    ('proof',         {'proof', 'proofonclose'}),
    ('audits',        {'audits', 'audit'}),
    ('testing',       {'testing', 'testdebt', 'testhygiene'}),
    ('skills',        {'skills'}),
    ('memory-store',  {'memory', 'store', 'consolidation'}),
    ('graph-embed',   {'graphembed'}),
    ('architecture',  {'architecture', 'architectureascode'}),
    ('observability', {'observability', 'system-alarms'}),
    ('tooling',       {'tooling', 'devops', 'launcher', 'infrastructure', 'env-hygiene', 'mcp'}),
    ('process',       {'process', 'devprocess', 'values', 'externalstate'}),
    ('workflow',      {'workflow'}),
]
def theme_of(tags):
    nt = {norm(t) for t in tags}
    for name, keys in THEME_MAP:
        if nt & keys:
            return name
    return '(other)' if tags else '(untagged)'

import re as _re
def clean_title(t):
    """Titles are stored with a leading '[status] ' that duplicates the status field."""
    return _re.sub(r'^\s*\[[a-z_]+\]\s*', '', t or '')

def wrap(text, indent, width=96):
    return '\n'.join(textwrap.wrap(' '.join((text or '').split()),
        width=width, initial_indent=' '*indent, subsequent_indent=' '*(indent+4))) or ' '*indent + '(none)'

# ── gather ────────────────────────────────────────────────────────────────
formal = []
for f in sorted(glob.glob(f'{ROOT}/intentions/*.json')):
    formal.append(json.load(open(f)).get('body', {}))

arch = {}
for f in sorted(glob.glob(f'{ROOT}/architecture/*.json')):
    b = json.load(open(f)).get('body', {})
    arch[b.get('subsystem', '?')] = b

open_t, closed_t = [], []
for f in glob.glob(f'{ROOT}/tickets/**/*.json', recursive=True):
    try: b = json.load(open(f)).get('body', {})
    except Exception: continue
    if not b.get('intention'): continue
    (closed_t if b.get('status') in TERMINAL else open_t).append(b)

by_dev = collections.defaultdict(list)
by_theme = collections.defaultdict(list)
for b in open_t:
    devs = attribute(b.get('id',''), b.get('title',''), b.get('tags') or [])
    if devs:
        for d in devs:
            by_dev[d].append(b)
    else:
        by_theme[theme_of(b.get('tags') or [])].append(b)

arch_by_dev = collections.defaultdict(list)
arch_crosscut = []
for sub, b in arch.items():
    devs = attribute(sub, b.get('title',''), b.get('related') or [])
    if devs:
        for d in devs:
            arch_by_dev[d].append(b)
    else:
        arch_crosscut.append(b)

# ── render ────────────────────────────────────────────────────────────────
L = []
w = L.append
w("UNSEEN UNIVERSITY — EVERY INTENTION WE HAVE A RECORD FOR, BY DEVICE")
w("=" * 78)
w(f"generated  {datetime.date.today().isoformat()} by CC.0")
w("")
w("WHAT THIS IS")
w("    Three kinds of record in devlab/runtime/memory/ carry an intention. All three are here.")
w("")
w(f"      intentions/       {len(formal):4}  formal I-xxx intentions (the root artifacts)")
w(f"      architecture/     {len(arch):4}  intention-points (intent -> implementing files)")
w(f"      tickets/          {len(open_t):4}  OPEN tickets with an 'I intend that...' statement")
w("")
w("SCOPE — read this before reviewing")
w(f"    Included in full: all {len(formal) + len(arch) + len(open_t)} records above.")
w(f"    Listed by title only in APPENDIX A: {len(closed_t)} CLOSED/DONE tickets that also carry an")
w("    intention. They are not dropped — say the word and I will expand any of them. They are")
w("    collapsed because a review set of ~560 statements is not a review set.")
w("")
w("HOW A TICKET WAS ATTRIBUTED TO A DEVICE — and where it is unreliable")
w("    A ticket is attributed by (a) a tag matching a device name, (b) the device name inside the")
w("    ticket id, or (c) for DISTINCTIVE device names only, the name appearing in the title.")
w("    Device names that are ordinary English words (queue, reader, critic, policy, intent,")
w("    template, workspace, sensor, minion, claude, postgres) are matched ONLY on tag or id —")
w("    matching those in free text would attribute half the backlog to 'queue'.")
w("    A ticket touching several devices appears under EACH of them, so Section 2's count exceeds")
w("    the number of distinct tickets. A ticket with no device evidence at all goes to SECTION 3,")
w("    grouped by dev-process theme — not to a junk bucket.")
n_dev_t = sum(len(v) for v in by_dev.values())
n_theme_t = sum(len(v) for v in by_theme.values())
w("")
w("INDEX")
w(f"    SECTION 1  formal intentions (I-xxx)                     {len(formal):4}")
w(f"    SECTION 2  by DEVICE                                     {n_dev_t:4} ticket intentions"
  f" across {len({k for k in set(by_dev)|set(arch_by_dev)})} devices")
w(f"    SECTION 2b cross-cutting architecture intention-points   {len(arch_crosscut):4}")
w(f"    SECTION 3  by DEV-PROCESS THEME (no device)              {n_theme_t:4} ticket intentions")
w(f"    APPENDIX A closed/done tickets carrying an intention     {len(closed_t):4} (titles only)")
w("")
w("    NOTE ON SECTION 3. These are not misfiled. They are intentions about the PROCESS —")
w("    proof-on-close, the workflow, consequence checks, the skills, the memory store — and the")
w("    process has no device. That more than half the open intentions live here is itself the")
w("    finding: the dev process is the largest unowned subsystem in the project.")
w("")
w("")
w("SECTION 1 — FORMAL INTENTIONS (I-xxx)   [cross-cutting; these are the roots]")
w("-" * 78)
for b in formal:
    w("")
    w(f"    {b.get('intention_id')}")
    w(f"        title    {b.get('title','')}")
    w(f"        status   {b.get('status','?')}        dated {b.get('date','?')}")
    w("        statement")
    w(wrap(b.get('statement',''), 12))
    rel = b.get('related') or {}
    if rel:
        w("        related")
        for k, v in rel.items():
            if v: w(wrap(f"{k}: " + ', '.join(v if isinstance(v, list) else [str(v)]), 12))
w("")
w("")
w("SECTION 2 — BY DEVICE")
w("-" * 78)

order = sorted(set(by_dev) | set(arch_by_dev))
for dev in order:
    tickets = sorted(by_dev.get(dev, []), key=lambda b: (-float(b.get('priority') or 0), b.get('id','')))
    points  = arch_by_dev.get(dev, [])
    w("")
    w(f"  DEVICE: {dev}    [{len(points)} intention-point(s), {len(tickets)} open ticket intention(s)]")
    w("  " + "-" * 74)
    for b in points:
        w("")
        w(f"      ARCHITECTURE INTENTION-POINT: {b.get('subsystem')}   (kind: {b.get('kind','?')})")
        w(wrap(b.get('title',''), 10))
        w("          summary")
        w(wrap(b.get('summary',''), 14))
        if b.get('owns'):
            w("          owns")
            for o in b['owns']: w(wrap(f"- {o}", 14))
        if b.get('status_notes'):
            w("          status notes")
            w(wrap(b['status_notes'], 14))
    if tickets:
        w("")
        w(f"      OPEN TICKET INTENTIONS")
    for b in tickets:
        w("")
        w(f"          {b.get('id')}   [{b.get('status')}] pri={b.get('priority','?')} size={b.get('size','?')}")
        w(wrap(clean_title(b.get('title','')), 14))
        w(wrap(b.get('intention',''), 14))

w("")
w("")
w("SECTION 2b — CROSS-CUTTING ARCHITECTURE INTENTION-POINTS (no single device owns these)")
w("-" * 78)
for b in sorted(arch_crosscut, key=lambda b: b.get('subsystem','')):
    w("")
    w(f"      INTENTION-POINT: {b.get('subsystem')}   (kind: {b.get('kind','?')})")
    w(wrap(b.get('title',''), 10))
    w("          summary")
    w(wrap(b.get('summary',''), 14))
    if b.get('owns'):
        w("          owns")
        for o in b['owns']: w(wrap(f"- {o}", 14))

w("")
w("")
w("SECTION 3 — DEV-PROCESS INTENTIONS (no device owns these)")
w("-" * 78)
for theme in sorted(by_theme, key=lambda t: (-len(by_theme[t]), t)):
    ts = sorted(by_theme[theme], key=lambda b: (-float(b.get('priority') or 0), b.get('id','')))
    w("")
    w(f"  THEME: {theme}    [{len(ts)} open ticket intention(s)]")
    w("  " + "-" * 74)
    for b in ts:
        w("")
        w(f"      {b.get('id')}   [{b.get('status')}] pri={b.get('priority','?')} size={b.get('size','?')}")
        w(wrap(clean_title(b.get('title','')), 10))
        w(wrap(b.get('intention',''), 10))

w("")
w("")
w("APPENDIX A — CLOSED / DONE TICKETS THAT CARRY AN INTENTION  (title only)")
w("-" * 78)
w(f"    {len(closed_t)} records. Ask and I will expand any of them in full.")
w("")
for b in sorted(closed_t, key=lambda b: b.get('id','')):
    w(f"    {b.get('id','?'):58} [{b.get('status','?')}]")

out = os.path.expanduser(os.environ.get('OUTLINE_OUT',
    f'~/.unseen_university/akien/inbox/{datetime.date.today():%Y%m%d}.CCReviewOfIntentionsOutline.txt'))
open(out, 'w').write('\n'.join(L) + '\n')
print("wrote", out)
print("lines:", len(L))
print(f"formal={len(formal)} arch={len(arch)} open_tickets={len(open_t)} closed={len(closed_t)}")
print("devices:", len(order), "| themes:", len(by_theme),
      "| device-attributed:", sum(len(v) for v in by_dev.values()),
      "| process-themed:", sum(len(v) for v in by_theme.values()))
