#!/usr/bin/env python3
"""stuck_corpus_label_page.py — one-keystroke human labeling for a sealed stuck corpus.

The classifier's quality eval (T-ds-defeating-question-classifier) grades against
Akien's labels over a SEALED eval slice (stuck-corpus-v2). This tool turns the label
pass from "read 31 raw JSONL records" into a single local HTML page: one transcript
entry at a time, d/c/u keystroke labels, auto-advance, export labels.json.

Why a generator (not a server): the slice is flat-file and the label pass is a
one-sitting human task — a self-contained page keeps the whole tool inspectable and
adds zero runtime surface. Why only the NEW messages per turn are embedded: the loop
re-sends the whole history every turn, so embedding raw requests would duplicate the
transcript ~n²/2; the trailing non-assistant messages per request ARE the turn's new
information (tool results in, action out).

Usage:
  python3 devlab/claudecode/stuck_corpus_label_page.py                 # generate page
  python3 devlab/claudecode/stuck_corpus_label_page.py --validate F    # check labels.json
  python3 devlab/claudecode/stuck_corpus_label_page.py --validate F --install
                                                                       # + copy into slice dir

The page writes nothing itself (labels autosave to localStorage, keyed by the slice
content_hash); Export downloads labels.json, which --validate/--install joins 1:1
against the manifest before it may land beside the slice. The sealed slice content is
never modified — labels are a NEW artifact.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
SLICE_DIR = _REPO_ROOT / "devlab/runtime/memory/eval_slices/stuck-corpus-v2"
LABELS_SCHEMA = "inference.stuck_labels.v1"
VALID_LABELS = {"design_stuck", "capability_stuck", "unsure"}


def _corpus_files() -> list[Path]:
    from unseen_university.devices.inference.io_corpus import corpus_root
    return sorted(corpus_root().glob("*.io.jsonl"))


def _load_manifest() -> dict:
    return json.loads((SLICE_DIR / "manifest.json").read_text(encoding="utf-8"))


def _collect_entries(manifest: dict) -> list[dict]:
    """Fetch the manifest's corpus records; hard-error if any id is missing."""
    wanted = set(manifest["entry_ids"])
    found: dict[str, dict] = {}
    for path in _corpus_files():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("id") in wanted:
                    found[rec["id"]] = rec
    missing = wanted - set(found)
    if missing:
        sys.exit(f"ERROR: {len(missing)} manifest ids not found in the corpus: {sorted(missing)[:3]}…")
    return list(found.values())


def _new_messages(request: dict) -> list[dict]:
    """The trailing non-assistant messages — this turn's NEW information.

    Turn 0: the initial task message. Later turns: the tool results returned for the
    previous assistant action (everything after the final assistant message).
    """
    msgs = request.get("messages") or []
    tail: list[dict] = []
    for m in reversed(msgs):
        if m.get("role") == "assistant":
            break
        tail.append(m)
    return list(reversed(tail))


def _assistant_view(response) -> dict:
    """Flatten the response into {content, tool_calls:[{name,args}]} for rendering."""
    msg = {}
    if isinstance(response, dict):
        msg = (response.get("raw") or {}).get("message") or response.get("message") or {}
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                pass
        calls.append({"name": fn.get("name", "?"), "args": args})
    return {"content": (msg.get("content") or "").strip(), "tool_calls": calls}


def _page_entries(records: list[dict]) -> list[dict]:
    """Corpus records -> render-ready entries, grouped by ticket, transcript order."""
    records = sorted(records, key=lambda r: (r.get("ticket_id", ""), r.get("ts", "")))
    out = []
    prev_ticket = None
    for r in records:
        req = r.get("request") or {}
        first_of_run = r.get("ticket_id") != prev_ticket
        prev_ticket = r.get("ticket_id")
        out.append({
            "id": r["id"],
            "ticket_id": r.get("ticket_id", "?"),
            "ts": r.get("ts", ""),
            "model": r.get("model", ""),
            "outcome": r.get("outcome", ""),
            "elapsed_ms": r.get("elapsed_ms"),
            "system": (req.get("system") or "") if first_of_run else "",
            "new_messages": [
                {"role": m.get("role", "?"), "content": str(m.get("content", ""))}
                for m in _new_messages(req)
            ],
            "assistant": _assistant_view(r.get("response")),
        })
    return out


def generate(out_path: Path) -> None:
    manifest = _load_manifest()
    entries = _page_entries(_collect_entries(manifest))
    data = {
        "slice": manifest["name"],
        "content_hash": manifest["content_hash"],
        "n": manifest["n"],
        "entries": entries,
    }
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = _HTML_TEMPLATE.replace("__TITLE__", html.escape(manifest["name"])).replace("__DATA__", blob)
    out_path.write_text(page, encoding="utf-8")
    print(f"wrote {out_path}  ({out_path.stat().st_size // 1024} KB, {len(entries)} entries)")
    print(f"open with:  xdg-open {out_path}")


def validate(labels_path: Path, install: bool) -> None:
    manifest = _load_manifest()
    doc = json.loads(labels_path.read_text(encoding="utf-8"))
    problems = []
    if doc.get("schema") != LABELS_SCHEMA:
        problems.append(f"schema is {doc.get('schema')!r}, want {LABELS_SCHEMA!r}")
    if doc.get("content_hash") != manifest["content_hash"]:
        problems.append("content_hash does not match the sealed manifest")
    labels = doc.get("labels") or {}
    manifest_ids, label_ids = set(manifest["entry_ids"]), set(labels)
    if label_ids - manifest_ids:
        problems.append(f"{len(label_ids - manifest_ids)} unknown ids (not in manifest)")
    if manifest_ids - label_ids:
        problems.append(f"{len(manifest_ids - label_ids)} manifest ids unlabeled")
    bad = {v for v in labels.values() if v not in VALID_LABELS}
    if bad:
        problems.append(f"invalid label values: {sorted(bad)}")
    if problems:
        print("INVALID labels.json:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    from collections import Counter
    print(f"VALID: {len(labels)}/{manifest['n']} labeled, 1:1 with manifest — {dict(Counter(labels.values()))}")
    if install:
        dest = SLICE_DIR / "labels.json"
        shutil.copy(labels_path, dest)
        print(f"installed -> {dest}")


_HTML_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Label: __TITLE__</title>
<style>
:root { --bg:#fff; --fg:#1a1a1a; --dim:#667; --card:#f5f6f8; --edge:#d8dbe2;
        --design:#7c4dcc; --capability:#0e8a6c; --unsure:#b08000; --accent:#2563eb; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14161a; --fg:#e8eaee; --dim:#98a0ae; --card:#1e2128; --edge:#333845;
          --design:#a78bfa; --capability:#34d399; --unsure:#fbbf24; --accent:#60a5fa; } }
* { box-sizing:border-box; }
body { margin:0; font:14px/1.5 system-ui,sans-serif; background:var(--bg); color:var(--fg);
       display:grid; grid-template-columns:290px 1fr; height:100vh; }
#side { border-right:1px solid var(--edge); overflow-y:auto; padding:10px; }
#main { overflow-y:auto; padding:18px 26px 120px; }
h1 { font-size:15px; margin:4px 0 10px; }
.runhdr { font-weight:600; font-size:12px; color:var(--dim); margin:12px 0 4px; word-break:break-all; }
.item { padding:3px 8px; border-radius:6px; cursor:pointer; display:flex; gap:6px; align-items:center;
        font-size:12px; color:var(--dim); }
.item.cur { background:var(--card); color:var(--fg); outline:1px solid var(--accent); }
.chip { width:10px; height:10px; border-radius:50%; background:var(--edge); flex:none; }
.chip.design_stuck { background:var(--design); } .chip.capability_stuck { background:var(--capability); }
.chip.unsure { background:var(--unsure); }
#bar { position:fixed; bottom:0; left:290px; right:0; background:var(--card);
       border-top:1px solid var(--edge); padding:10px 26px; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
button { font:inherit; padding:6px 14px; border-radius:8px; border:1px solid var(--edge);
         background:var(--bg); color:var(--fg); cursor:pointer; }
button.d { border-color:var(--design); } button.c { border-color:var(--capability); }
button.u { border-color:var(--unsure); } button.x { border-color:var(--accent); font-weight:600; }
kbd { background:var(--card); border:1px solid var(--edge); border-radius:4px; padding:0 5px; font-size:11px; }
.meta { color:var(--dim); font-size:12px; margin-bottom:10px; }
.msg { background:var(--card); border:1px solid var(--edge); border-radius:10px;
       padding:10px 14px; margin:10px 0; overflow-x:auto; }
.msg .who { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:var(--dim); margin-bottom:6px; }
pre { margin:0; white-space:pre-wrap; word-break:break-word; font:12px/1.45 ui-monospace,monospace; }
.tool { border-left:3px solid var(--accent); }
.assistant { border-left:3px solid var(--capability); }
details.sys { margin:8px 0; } details.sys pre { color:var(--dim); }
.lbl { font-weight:700; }
.lbl.design_stuck { color:var(--design); } .lbl.capability_stuck { color:var(--capability); }
.lbl.unsure { color:var(--unsure); }
#prog { margin-left:auto; color:var(--dim); font-size:12px; }
</style></head><body>
<nav id="side"></nav>
<section id="main"></section>
<div id="bar">
  <button class="d" onclick="label('design_stuck')"><kbd>d</kbd> design_stuck</button>
  <button class="c" onclick="label('capability_stuck')"><kbd>c</kbd> capability_stuck</button>
  <button class="u" onclick="label('unsure')"><kbd>u</kbd> unsure</button>
  <span style="color:var(--dim);font-size:12px">shift = whole run &nbsp;·&nbsp; <kbd>←</kbd><kbd>→</kbd> navigate</span>
  <button class="x" onclick="exportLabels()">Export labels.json</button>
  <span id="prog"></span>
</div>
<script>
const DATA = __DATA__;
const KEY = 'stuck-labels-' + DATA.content_hash;
let labels = JSON.parse(localStorage.getItem(KEY) || '{}');
let cur = 0;
const E = DATA.entries;

function esc(s){ const d=document.createElement('span'); d.textContent=s??''; return d.innerHTML; }
function clip(s,n){ s=String(s??''); return s.length>n ? s.slice(0,n)+'\n… ['+(s.length-n)+' more chars]' : s; }

function save(){ localStorage.setItem(KEY, JSON.stringify(labels)); }
function label(v, whole){
  if (whole===undefined) whole = window.event && window.event.shiftKey;
  if (whole){ const t=E[cur].ticket_id; E.forEach(e=>{ if(e.ticket_id===t) labels[e.id]=v; }); }
  else labels[E[cur].id]=v;
  save(); if (cur < E.length-1) cur++; render();
}
function exportLabels(){
  const doc = { schema:'inference.stuck_labels.v1', slice:DATA.slice, content_hash:DATA.content_hash,
                n:DATA.n, labeled_by:'akien', exported_at:new Date().toISOString(), labels:labels };
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(doc,null,2)],{type:'application/json'}));
  a.download = 'labels.json'; a.click();
}
function render(){
  const side = document.getElementById('side');
  let h = '<h1>'+esc(DATA.slice)+'</h1>'; let lastT=null;
  E.forEach((e,i)=>{
    if (e.ticket_id!==lastT){ h+='<div class="runhdr">'+esc(e.ticket_id)+'</div>'; lastT=e.ticket_id; }
    h+='<div class="item'+(i===cur?' cur':'')+'" onclick="cur='+i+';render()">'
      +'<span class="chip '+(labels[e.id]||'')+'"></span>#'+(i+1)
      +' <span>'+(e.assistant.tool_calls.map(t=>t.name).join(',')||'text')+'</span></div>';
  });
  side.innerHTML = h;
  const e = E[cur];
  let m = '<div class="meta">entry '+(cur+1)+'/'+E.length+' · <b>'+esc(e.ticket_id)+'</b> · '
        + esc(e.model)+' · '+esc(e.outcome)+' · '+(e.elapsed_ms??'?')+'ms · '
        + (labels[e.id] ? 'label: <span class="lbl '+labels[e.id]+'">'+labels[e.id]+'</span>' : 'unlabeled')
        + '</div>';
  if (e.system) m += '<details class="sys"><summary>system prompt (run start)</summary><pre>'
                   + esc(clip(e.system,6000))+'</pre></details>';
  e.new_messages.forEach(msg=>{
    m += '<div class="msg '+(msg.role==='tool'?'tool':'')+'"><div class="who">'+esc(msg.role)
       + (msg.role==='tool'?' result (in)':'')+'</div><pre>'+esc(clip(msg.content,4000))+'</pre></div>';
  });
  const a = e.assistant;
  m += '<div class="msg assistant"><div class="who">assistant (out)</div>';
  if (a.content) m += '<pre>'+esc(clip(a.content,4000))+'</pre>';
  a.tool_calls.forEach(t=>{
    m += '<pre><b>→ '+esc(t.name)+'</b> '+esc(clip(JSON.stringify(t.args,null,1),2500))+'</pre>';
  });
  if (!a.content && !a.tool_calls.length) m += '<pre>(empty response)</pre>';
  m += '</div>';
  document.getElementById('main').innerHTML = m;
  const done = Object.keys(labels).filter(k=>E.some(e=>e.id===k)).length;
  document.getElementById('prog').textContent = done+'/'+E.length+' labeled';
  document.getElementById('main').scrollTop = 0;
}
document.addEventListener('keydown', ev=>{
  if (ev.key==='ArrowRight'||ev.key==='j'){ if(cur<E.length-1){cur++;render();} }
  else if (ev.key==='ArrowLeft'||ev.key==='k'){ if(cur>0){cur--;render();} }
  else if (ev.key==='d'||ev.key==='D') label('design_stuck', ev.shiftKey);
  else if (ev.key==='c'||ev.key==='C') label('capability_stuck', ev.shiftKey);
  else if (ev.key==='u'||ev.key==='U') label('unsure', ev.shiftKey);
});
render();
</script></body></html>
"""


def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default=str(SLICE_DIR / "label_page.html"),
                    help="output HTML path (default: label_page.html beside the slice; gitignored)")
    ap.add_argument("--validate", metavar="LABELS_JSON",
                    help="validate an exported labels.json against the sealed manifest")
    ap.add_argument("--install", action="store_true",
                    help="with --validate: copy the validated labels.json into the slice dir")
    args = ap.parse_args()
    if args.validate:
        validate(Path(args.validate).expanduser(), args.install)
    else:
        generate(Path(args.out).expanduser())


if __name__ == "__main__":
    _main()
