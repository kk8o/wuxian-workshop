r"""Builds the "api" content pack (content.py, apidocs.py) from 无限图鉴's API manual (the api/ folder of the wuxianwow.com
data repository, which is not part of this one; the built pack is src/wuxianworkshop/data/api.json.gz): the API
documentation it extracted from the client (apidocs.json, with the client it came from), the exe's `Usage:` strings
(api_usage.txt), what the last client builds changed in it (changes.json, newest first), the protected global functions
the page lists, and the manual text of its page (source/build_page.py: the 运行时 and 插件加载与限制 tabs, with the name
lists filled in, and MANUAL_EN, the same in English; read with ast, never run). Writes the program's own copy (src\wuxianworkshop\data\api.json.gz) and the copy for the website
(dist\content\api-<version>.json.gz with manifest.json beside it, for wuxianwow.com/workshop/data/).

    .venv\Scripts\python.exe scripts\build_api_pack.py --codex <that repository> [--version 2026.10.06.1]
"""
import argparse
import ast
import gzip
import hashlib
import html
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = ROOT / "src" / "wuxianworkshop" / "data" / "api.json.gz"
OUT = ROOT / "dist" / "content"
SOURCE = "Blizzard_APIDocumentationGenerated（从客户端提取）、exe 里的 Usage 字符串、无限图鉴整理的手册"
TOPIC_IDS = {"运行时": "runtime", "可用的标准库": "stdlib", "WoW 扩展的全局函数": "globals", "安全与污染（taint）": "taint",
             "机密值（secret values）": "secret", "资源配额": "quotas", "相关 CVar": "cvars", "TOC 文件": "toc",
             "加载开关": "load-switches", "受保护的全局函数": "protected", "安全模板": "secure-templates",
             "与无限相关的 GameRule": "gamerules", "无限专属 API": "forever-api"}


def constants(tree):
    """module-level NAME = "a b c".split() / [..] assignments of the page builder (the last one of a name wins)"""
    out = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        name, v = node.targets[0].id, node.value
        if (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "split"
                and isinstance(v.func.value, ast.Constant) and isinstance(v.func.value.value, str)):
            sep = v.args[0].value if v.args and isinstance(v.args[0], ast.Constant) else None
            out[name] = v.func.value.value.split(sep)
        elif isinstance(v, ast.List):
            try:
                out[name] = ast.literal_eval(v)
            except ValueError:
                pass
        elif isinstance(v, ast.Constant) and isinstance(v.value, str) and name not in out:
            out[name] = v.value
    return out


def to_markdown(fragment):
    """the little HTML of the manual as Markdown: headings, paragraphs, lists, code, bold"""
    s = re.sub(r"\s+", " ", fragment)
    s = re.sub(r"<code[^>]*>(.*?)</code>", lambda m: "`" + m.group(1) + "`", s)
    s = re.sub(r"<b>(.*?)</b>", r"**\1**", s)
    s = re.sub(r"<h2[^>]*>(.*?)</h2>", r"\n\n## \1\n\n", s)
    s = re.sub(r"<h3[^>]*>(.*?)</h3>", r"\n\n### \1\n\n", s)
    s = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", s)
    s = re.sub(r"</?(p|div|ul|section)[^>]*>", "\n\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n[ \t]+", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def topics(md):
    """the Markdown cut at every heading: [{id, title, md}]"""
    out, cur = [], None
    for line in md.splitlines():
        m = re.match(r"^(##|###) (.+)$", line)
        if m:
            title = m.group(2).strip()
            cur = dict(id=TOPIC_IDS.get(title) or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or f"t{len(out)}",
                       title=title, lines=[])
            out.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    return [dict(id=t["id"], title=t["title"], md="\n".join(t["lines"]).strip()) for t in out]


def manual(codex, docs):
    """(the topics with their English text, the client the documentation came from, the protected globals)"""
    src = (codex / "api" / "source" / "build_page.py").read_text(encoding="utf-8")
    c = constants(ast.parse(src))
    page = c["page"]
    fns = [f for n in docs["namespaces"] for f in n["functions"]]
    counts = {"__NNS__": len({n["ns"] for n in docs["namespaces"] if n["ns"]}), "__NF__": len(fns),
              "__NE__": sum(len(n["events"]) for n in docs["namespaces"]),
              "__NT__": sum(len(n["tables"]) for n in docs["namespaces"]),
              "__NPROT__": sum(1 for f in fns if (f.get("raw") or {}).get("IsProtectedFunction"))}
    lists = {"__LIBS__": c["LIBS"], "__WOWGLOBALS__": c["WOWGLOBALS"], "__SECUREFN__": c["SECUREFN"],
             "__CVARS__": c["CVARS"], "__TOCDIRS__": ["## " + x for x in c["TOCDIRS"]],
             "__PROTGLOBALS__": c["PROTECTED_GLOBALS"], "__SECTPL__": c["SECURE_TEMPLATES"], "__GAMERULES__": c["GAMERULES_UI"]}
    def text(html_, sep):
        md = to_markdown("".join(re.findall(r'<section id="tab-(?:env|rules)"[^>]*>(.*?)</section>', html_, re.S)))
        for token, items in lists.items():
            md = md.replace(token, sep.join(f"`{x}`" for x in items))
        for token, n in counts.items():
            md = md.replace(token, str(n))
        return md
    zh = topics(text(page, "、"))
    if c.get("MANUAL_EN"):                       # the English text: the same headings in the same order
        en = topics(text(c["MANUAL_EN"], ", "))
        if len(en) != len(zh):
            raise SystemExit(f"MANUAL_EN has {len(en)} topics, the Chinese manual {len(zh)}: keep the two in step")
        for t, e in zip(zh, en):
            t["title_en"], t["md_en"] = e["title"], e["md"]
    client = (docs.get("client") or {}).get("build")
    if not client:                               # a documentation file from before update.py stamped it
        m = re.search(r'<div class="k">客户端版本</div><div class="v">([^<_]+)</div>', page)
        client = m.group(1) if m else None
    return zh, client, c.get("PROTECTED_GLOBALS", [])


# the words 无限图鉴's api/source/update.py describe() writes a change note with, in English
NOTE_WORDS = (("新增标记 ", "new flag "), ("去掉标记 ", "flag removed: "), ("说明文字改了", "description changed"),
              ("无返回值", "no returns"), ("参数 ", "arguments "), ("返回值 ", "returns "), ("载荷 ", "payload "),
              ("新增 ", "added "), ("删除 ", "removed "), ("改为 ", "changed "), ("；", "; "), ("、", ", "), ("，", ", "))


def note_en(note):
    """a change note in English: its fixed words replaced, "N 项" as "N entries", a lone 无 as none"""
    for zh, en in NOTE_WORDS:
        note = note.replace(zh, en)
    note = re.sub(r"(\d+) 项", r"\1 entries", note)
    return re.sub(r"(?<![\w\u4e00-\u9fff])无(?![\w\u4e00-\u9fff])", "none", note)


def build(codex, version):
    docs = json.loads((codex / "api" / "apidocs.json").read_text(encoding="utf-8"))
    usage = [line.strip() for line in (codex / "api" / "api_usage.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    man, client, protected = manual(codex, docs)
    cpath = codex / "api" / "changes.json"
    changes = json.loads(cpath.read_text(encoding="utf-8")) if cpath.is_file() else []
    for record in changes:                         # the notes in English as well (the App's English pages)
        for item in record.get("added", []) + record.get("changed", []):
            if item.get("note"):
                item["note_en"] = note_en(item["note"])
    return dict(pack="api", version=version, client=client, interface=16001, built=time.strftime("%Y-%m-%d"),
                source=SOURCE, namespaces=docs["namespaces"], usage=usage, manual=man, changes=changes,
                protected_globals=protected)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--codex", default=os.environ.get("WUXIAN_CODEX"), required=not os.environ.get("WUXIAN_CODEX"),
                    help="the checkout of the wuxianwow.com data repository (or WUXIAN_CODEX)")
    ap.add_argument("--version", default=time.strftime("%Y.%m.%d") + ".1", help="the pack's version (default: today.1)")
    args = ap.parse_args()
    pack = build(Path(args.codex), args.version)
    raw = gzip.compress(json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9, mtime=0)
    BUNDLED.parent.mkdir(parents=True, exist_ok=True)
    BUNDLED.write_bytes(raw)
    OUT.mkdir(parents=True, exist_ok=True)
    name = f"api-{args.version}.json.gz"
    (OUT / name).write_bytes(raw)
    mpath = OUT / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else {"packs": {}}
    manifest.setdefault("packs", {})["api"] = dict(version=args.version, file=name, size=len(raw),
                                                    sha256=hashlib.sha256(raw).hexdigest(), min_app="0.8.0")
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fns = sum(len(n["functions"]) for n in pack["namespaces"])
    print(f"api {args.version}: {len(pack['namespaces'])} namespaces, {fns} functions, {len(pack['usage'])} usage strings, "
          f"{len(pack['manual'])} manual topics ({sum(1 for m in pack['manual'] if m.get('md_en'))} in English), "
          f"{len(pack['changes'])} change records, client {pack['client']}; {len(raw) / 1024:.0f} KB -> {BUNDLED} and {OUT / name}")


if __name__ == "__main__":
    main()
