#!/usr/bin/env python3
"""Generate judges.html — the complete judge reference — from this folder's rubrics/.

Self-contained: reads only ./rubrics/*.md. Run it after any rubric change so the
HTML never drifts from the prompts that actually deploy.

    python3 tools/build_judge_docs.py
"""
from __future__ import annotations
import html, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUB = ROOT / "rubrics"
FM = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)

def md(t: str) -> str:
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![*\w])\*([^*]+?)\*(?!\*)", r"<i>\1</i>", t)
    return re.sub(r"`([^`]+?)`", r"<code>\1</code>", t)

def parse(p: pathlib.Path):
    m = FM.match(p.read_text(encoding="utf-8"))
    if not m: return None
    import yaml
    meta = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)
    def section(name):
        mm = re.search(rf"^## {re.escape(name)}\n(.*?)(?=\n## |\Z)", body, re.S | re.M)
        return mm.group(1).strip() if mm else ""
    def bullets(txt):
        return [" ".join(x.split()) for x in re.findall(r"^- (.+?)(?=\n- |\n\n|\Z)", txt, re.M | re.S)]
    hard = re.search(r"\*\*0 — Hard failure\.\*\*(.*?)(?=\n## |\Z)", body, re.S)
    bands = {}
    for lbl, key in (("1.0 — Pass", "pass"), ("0.5 — Warning", "warn"), ("0 — Hard failure", "fail")):
        mm = re.search(rf"\*\*{re.escape(lbl)}\.\*\*(.*?)(?=\n\*\*[01]|\n## |\Z)", body, re.S)
        bands[key] = " ".join(mm.group(1).split())[:400] if mm else ""
    return {
        "id": meta["id"], "name": meta.get("name", meta["id"]),
        "unit": meta.get("unit", "turn"), "tier": meta.get("tier", ""),
        "variant_of": meta.get("variant_of"),
        "trigger": " ".join(str(meta.get("trigger", "")).split()),
        "checks": meta.get("checks", []),
        "extra": [e["name"] for e in meta.get("extra_fields", [])],
        "grounding": meta.get("grounding", []),
        "deconf": [" ".join(str(x).split()) for x in meta.get("de_confliction", [])],
        "measures": " ".join(section("What this measures").split())[:700],
        "absent": " ".join(section("Scoring when the trigger is absent").split())[:500],
        "bright": bullets(hard.group(1).strip()) if hard else [],
        "bands": bands,
    }

def build(title_suffix: str, blurb: str) -> str:
    judges = sorted((j for j in (parse(p) for p in sorted(RUB.glob("*.md"))
                                 if not p.name.startswith("_")) if j),
                    key=lambda j: (j["tier"], j["id"]))
    css = (ROOT / "tools" / "_doc.css")
    style = css.read_text() if css.exists() else DEFAULT_CSS
    out = [f'<!doctype html><html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           f'<title>Judge Reference</title>',
           '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Spectral:wght@400;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">',
           f'<style>{style}</style></head><body><div class="wrap">',
           f'<p class="eyebrow">Helpfulness judges · {html.escape(title_suffix)}</p>',
           '<h1>Judge Reference</h1>',
           f'<p class="lede">{blurb}</p>']

    out.append('<div class="scale"><div class="cap">The scale — one rule, every judge</div>')
    for v, w, d in (("0", "Hard failure", "A bright line named in the rubric was crossed. Absolute — no number of passing checks lifts it."),
                    ("0.5", "Warning", "One or more checks failed, no bright line fired. Worth fixing, nothing broken."),
                    ("1.0", "Pass", "Every check passed — or the dimension had nothing to get wrong and the assistant did not manufacture a problem.")):
        out.append(f'<div class="band"><span class="n">{v}</span><span class="d"><b>{w}.</b> {d}</span></div>')
    out.append('</div>')

    out.append('<h2>The suite</h2><div class="tw"><table><thead><tr><th>Judge</th><th>Unit</th>'
               '<th>Tier</th><th>Checks</th><th>Bright lines</th><th>Trigger</th></tr></thead><tbody>')
    for j in judges:
        v = ' <span class="chip warn">variant</span>' if j["variant_of"] else ""
        out.append(f'<tr><td class="jid"><a href="#{j["id"]}">{j["id"]}</a>{v}</td>'
                   f'<td class="m">{j["unit"]}</td><td class="m">{j["tier"]}</td>'
                   f'<td class="m">{len(j["checks"])}</td><td class="m">{len(j["bright"])}</td>'
                   f'<td class="sm">{md(j["trigger"][:130])}</td></tr>')
    out.append('</tbody></table></div>')

    for j in judges:
        out.append(f'<section class="judge" id="{j["id"]}"><div class="jh"><h3>{j["id"]}</h3>'
                   f'<span class="chip">{j["unit"]}</span><span class="chip">{j["tier"]}</span>'
                   + (f'<span class="chip warn">variant of {j["variant_of"]}</span>' if j["variant_of"] else "")
                   + '</div>')
        out.append(f'<p class="measures">{md(j["measures"])}</p>')
        out.append(f'<div class="trg"><b>Trigger</b>{md(j["trigger"])}</div>')
        if j["absent"]:
            out.append(f'<div class="trg alt"><b>When the trigger is absent</b>{md(j["absent"])}</div>')
        out.append(f'<h4>Binary checks ({len(j["checks"])})</h4><ol class="chk">')
        for c in j["checks"]:
            out.append(f'<li><span class="cid">{c["id"]}</span>{md(c["question"])}</li>')
        out.append('</ol>')
        out.append('<h4>Bright lines — any one forces 0</h4><ul class="bl">')
        for b in j["bright"]:
            out.append(f'<li>{md(b)}</li>')
        out.append('</ul>')
        out.append('<h4>Score bands</h4><div class="bands">')
        for k, lbl, cls in (("pass", "1.0 Pass", "p"), ("warn", "0.5 Warning", "w"), ("fail", "0 Hard failure", "f")):
            if j["bands"][k]:
                out.append(f'<div class="bd {cls}"><span class="bl-lbl">{lbl}</span>{md(j["bands"][k])}</div>')
        out.append('</div>')
        if j["extra"]:
            out.append('<h4>Extra fields</h4><p class="sm">' +
                       ", ".join(f"<code>{e}</code>" for e in j["extra"]) + "</p>")
        if j["deconf"]:
            out.append('<h4>Boundaries with other judges</h4><ul class="dc">' +
                       "".join(f"<li>{md(x)}</li>" for x in j["deconf"]) + "</ul>")
        out.append('<h4>Grounding</h4><ul class="gr">' +
                   "".join(f"<li>{md(g)}</li>" for g in j["grounding"]) + "</ul>")
        out.append('</section>')

    out.append('<footer><p>Generated from <code>rubrics/</code> by '
               '<code>tools/build_judge_docs.py</code>. Regenerate after any rubric change.</p></footer>')
    out.append('</div></body></html>')
    return "\n".join(out)

DEFAULT_CSS = """
:root{--g:#F2F4F1;--s:#FBFCFA;--sa:#E9ECE7;--ink:#15191A;--soft:#3A4341;--mut:#6A7370;
--r:#D2D8D2;--rs:#E2E6E0;--a:#0B5D51;--ai:#084A41;--aw:#DCE8E4;--w:#8A5715;--ww:#F2E7D4;
--f:#8C2F2A;--fw:#F3DEDC;--fd:"Spectral",Georgia,serif;--fb:"IBM Plex Sans",sans-serif;
--fm:"IBM Plex Mono",monospace}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--g:#0E1211;--s:#161B19;--sa:#1D2422;
--ink:#E7EBE7;--soft:#BDC5C1;--mut:#8B9591;--r:#2B3431;--rs:#222A28;--a:#63C3B0;--ai:#8AD6C5;
--aw:#172A27;--w:#D9A75C;--ww:#2A2317;--f:#E0857E;--fw:#2B1A19}}
*{box-sizing:border-box}body{margin:0;background:var(--g);color:var(--ink);font-family:var(--fb);
font-size:16px;line-height:1.6}
.wrap{max-width:1080px;margin:0 auto;padding:clamp(2rem,6vw,4rem) clamp(1rem,4vw,3rem) 5rem}
p{margin:0 0 1em;color:var(--soft)}code{font-family:var(--fm);font-size:.86em;background:var(--sa);
padding:.1em .35em;border-radius:2px;color:var(--ink)}
.eyebrow{font-family:var(--fm);font-size:.71rem;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);margin:0 0 1rem}
h1{font-family:var(--fd);font-weight:600;font-size:clamp(2rem,5vw,2.9rem);margin:0 0 .7rem;letter-spacing:-.02em}
.lede{font-family:var(--fd);font-size:1.08rem;color:var(--soft);max-width:64ch;margin:0 0 1.8rem}
h2{font-family:var(--fd);font-weight:600;font-size:1.5rem;margin:2.4rem 0 .4rem}
h4{font-family:var(--fb);font-size:.82rem;font-weight:600;text-transform:uppercase;letter-spacing:.07em;
color:var(--mut);margin:1.3rem 0 .5rem}
.scale{border:1px solid var(--r);background:var(--s);margin:0 0 1.6rem}
.cap{font-family:var(--fm);font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);
padding:.7rem 1rem;border-bottom:1px solid var(--rs)}
.band{display:grid;grid-template-columns:3rem 1fr;gap:.9rem;padding:.65rem 1rem;border-bottom:1px solid var(--rs)}
.band:last-child{border-bottom:none}.band .n{font-family:var(--fm);color:var(--ai);font-variant-numeric:tabular-nums}
.band .d{font-size:.92rem;color:var(--soft)}.band .d b{color:var(--ink)}
.tw{overflow-x:auto;border-top:2px solid var(--ink);border-bottom:1px solid var(--r);margin-top:1rem}
table{border-collapse:collapse;width:100%;min-width:760px}
th{font-family:var(--fm);font-size:.66rem;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);
text-align:left;font-weight:500;padding:.65rem .8rem .6rem 0;border-bottom:1px solid var(--r)}
td{padding:.6rem .8rem .6rem 0;border-bottom:1px solid var(--rs);font-size:.9rem;color:var(--soft);vertical-align:top}
td.jid a{font-family:var(--fm);font-size:.84rem;color:var(--ink);text-decoration:none;border-bottom:1px solid var(--r)}
td.jid a:hover{border-bottom-color:var(--a)}
td.m{font-family:var(--fm);font-size:.76rem;color:var(--mut);white-space:nowrap}
td.sm,.sm{font-size:.84rem;color:var(--mut)}
.chip{font-family:var(--fm);font-size:.68rem;padding:.22em .55em;border:1px solid var(--r);
border-radius:2px;color:var(--mut);background:var(--s);white-space:nowrap}
.chip.warn{color:var(--w);border-color:var(--w);background:var(--ww)}
.judge{border-top:2px solid var(--ink);margin-top:2.4rem;padding-top:1rem;scroll-margin-top:1rem}
.jh{display:flex;flex-wrap:wrap;gap:.5rem .8rem;align-items:center;margin-bottom:.7rem}
.jh h3{font-family:var(--fm);font-size:1.05rem;font-weight:500;margin:0;color:var(--ink)}
.measures{font-size:.95rem;max-width:72ch}
.trg{font-size:.87rem;color:var(--mut);border-left:2px solid var(--r);padding-left:.85rem;margin:.9rem 0;max-width:70ch}
.trg.alt{border-left-color:var(--a)}
.trg b{font-family:var(--fm);font-size:.7rem;letter-spacing:.09em;text-transform:uppercase;color:var(--a);
display:block;margin-bottom:.2rem;font-weight:500}
ol.chk{margin:0;padding:0;list-style:none;counter-reset:none}
ol.chk li{padding:.55rem 0 .55rem 3rem;border-bottom:1px solid var(--rs);font-size:.9rem;color:var(--soft);position:relative}
ol.chk li:last-child{border-bottom:none}
.cid{position:absolute;left:0;font-family:var(--fm);font-size:.76rem;color:var(--ai)}
ul.bl,ul.gr,ul.dc{margin:0;padding:0;list-style:none}
ul.bl li{padding:.5rem 0 .5rem 1.4rem;border-bottom:1px solid var(--rs);font-size:.89rem;color:var(--soft);position:relative}
ul.bl li::before{content:"";position:absolute;left:0;top:1em;width:.7rem;height:2px;background:var(--f)}
ul.bl li:last-child,ul.gr li:last-child,ul.dc li:last-child{border-bottom:none}
ul.gr li,ul.dc li{padding:.45rem 0;border-bottom:1px solid var(--rs);font-size:.84rem;color:var(--mut);line-height:1.5}
.bands{display:grid;gap:1px;background:var(--rs);border:1px solid var(--rs)}
@media(min-width:840px){.bands{grid-template-columns:repeat(3,1fr)}}
.bd{background:var(--s);padding:.75rem .9rem;font-size:.86rem;color:var(--soft)}
.bl-lbl{font-family:var(--fm);font-size:.72rem;display:block;margin-bottom:.35rem;font-weight:500}
.bd.p .bl-lbl{color:var(--a)}.bd.w .bl-lbl{color:var(--w)}.bd.f .bl-lbl{color:var(--f)}
footer{margin-top:3rem;padding-top:1.2rem;border-top:1px solid var(--r);font-size:.83rem;color:var(--mut)}
"""

if __name__ == "__main__":
    suffix = sys.argv[1] if len(sys.argv) > 1 else "reference"
    blurb = sys.argv[2] if len(sys.argv) > 2 else "Every judge in this folder: what it measures, its binary checks, its bright lines, and its score bands."
    (ROOT / "judges.html").write_text(build(suffix, blurb))
    print(f"wrote {ROOT/'judges.html'}")
