r"""Builds the "api" content pack (content.py, apidocs.py) from 无限图鉴's API manual (the api/ folder of the wuxianwow.com
data repository, which is not part of this one; the built pack is src/wuxianworkshop/data/api.json.gz): the API
documentation it extracted from the client (apidocs.json, with the client it came from), the exe's `Usage:` strings
(api_usage.txt), what the last client builds changed in it (changes.json, newest first), the protected global functions
the page lists, and the manual text of its page (source/build_page.py: the 运行时 and 插件加载与限制 tabs, with the name
lists filled in, and MANUAL_EN, the same in English; read with ast, never run). Writes the program's own copy (src\wuxianworkshop\data\api.json.gz) and the copy for the website
(dist\content\api-<version>.json.gz with manifest.json beside it, for wuxianwow.com/workshop/data/).
The pack's `runtime` is what the game has, from the scan `wuxian api-scan --out <that repository>\api\game_scan.json` made
in the running game of the documentation's build (runtime()): the documentation leaves functions out (C_Garrison has 227
in 1.60.1.70338, 40 documented) and lists some the game lacks (the login screen's); a pack without it (--no-scan) is read
as before. While the game of a new build cannot be played (the official client 1.60.1.70630 came on 2026-10-27, its
servers open on 11-05), --carry-scan takes the scan of an earlier build and applies to it what the documentation says
changed since (changes.json: functions and Enum / Constants tables added or removed); the pack's runtime keeps the build it
was scanned on and names the one it was carried to (carried).

    .venv\Scripts\python.exe scripts\build_api_pack.py --codex <that repository> [--version 2026.10.06.1] [--no-scan | --carry-scan]
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

from wuxianworkshop.apidocs import object_of

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = ROOT / "src" / "wuxianworkshop" / "data" / "api.json.gz"
OUT = ROOT / "dist" / "content"
SOURCE = "Blizzard_APIDocumentationGenerated（从客户端提取）、exe 里的 Usage 字符串、无限图鉴整理的手册"
SCAN = "game_scan.json"         # beside apidocs.json: what the game has (wuxian api-scan)
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
    protected_globals = set(c["PROTECTED_GLOBALS"])
    # as the page counts them: protected = secure code only (IsProtectedFunction but not an object's method, or one of the
    # protected globals); an object's method marked IsProtectedFunction is a protected method (apidocs.object_of)
    nprot = npmeth = 0
    for n in docs["namespaces"]:
        obj = None if n["ns"] else object_of(n["name"])
        for f in n["functions"]:
            if (f.get("raw") or {}).get("IsProtectedFunction"):
                npmeth += bool(obj)
                nprot += not obj
            elif not n["ns"] and not obj and f["name"] in protected_globals:
                nprot += 1
    counts = {"__NNS__": len({n["ns"] for n in docs["namespaces"] if n["ns"]}), "__NF__": len(fns),
              "__NE__": sum(len(n["events"]) for n in docs["namespaces"]),
              "__NT__": sum(len(n["tables"]) for n in docs["namespaces"]),
              "__NPROT__": nprot, "__NPMETH__": npmeth}
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


def runtime(scan, docs):
    """what the pack says the game has (apidocs.py shows what the documentation leaves out or the game lacks, lint.py
    takes it for the truth): the scan's names, with the documented ones an addon had set in the game it was scanned in
    (the scan leaves those out: the client's all the same), and the fields of the Enum and Constants tables the
    documentation does not have"""
    replaced = set(scan.get("replaced") or {})
    namespaces = {ns: set(names) for ns, names in scan["namespaces"].items()}
    found = set(scan["globals"])
    for n in docs["namespaces"]:
        for f in n["functions"]:
            if n["ns"].startswith("C_") and {n["ns"], f"{n['ns']}.{f['name']}"} & replaced:
                namespaces.setdefault(n["ns"], set()).add(f["name"])
            elif not n["ns"] and not object_of(n["name"]) and f["name"] in replaced:
                found.add(f["name"])
    kinds = {"Enumeration": ("Enum", "enums"), "Constants": ("Constants", "constants")}
    tables = {(t["type"], t["name"]) for n in docs["namespaces"] for t in n["tables"] if t["type"] in kinds}
    out = dict(client=scan["client"], scanned=scan["scanned"], globals=sorted(found),
               namespaces={ns: sorted(names) for ns, names in sorted(namespaces.items())})
    for kind, (base, key) in kinds.items():
        out[key] = sorted(set(scan[key]) | {name for k, name in tables if k == kind and f"{base}.{name}" in replaced})
    out["fields"] = {full: values for full, values in sorted((scan.get("fields") or {}).items())
                     if ("Enumeration" if full.startswith("Enum.") else "Constants", full.partition(".")[2]) not in tables}
    return out


def carry(scan, changes, docs, to):
    """the scan of an earlier build brought to the documentation's build `to` by the documentation's own changes since
    (the records of changes.json from the scan's build on): functions added or removed (namespaced or global), Enum and
    Constants tables added or removed; SystemExit when the records do not lead from the one build to the other"""
    chain, at = [], scan.get("client")
    for record in reversed(changes):                    # changes.json: the newest first
        if record.get("from") == at:
            chain.append(record)
            at = record.get("to")
    if at != to or not chain:
        raise SystemExit(f"changes.json does not lead from {scan.get('client')} (the scan) to {to} (the documentation)")
    kinds = {t["name"]: t["type"] for n in docs["namespaces"] for t in n["tables"]}
    out = dict(scan, namespaces={ns: list(names) for ns, names in scan["namespaces"].items()},
               globals=list(scan["globals"]), enums=list(scan["enums"]), constants=list(scan["constants"]))
    for record in chain:
        for side in ("added", "removed"):
            for item in record.get(side, []):
                what, name = item["what"], item["name"]
                if what.endswith("函数"):
                    ns, _, short = name.rpartition(".")
                    names = out["namespaces"].setdefault(ns, []) if ns else out["globals"]
                elif what.endswith(("枚举", "表")) and kinds.get(name, "Enumeration" if what.endswith("枚举") else None) \
                        in ("Enumeration", "Constants"):
                    short = name
                    names = out["enums" if kinds.get(name, "Enumeration") == "Enumeration" else "constants"]
                elif what.endswith(("枚举", "表")) and side == "removed":   # gone from the documentation: either list
                    for key in ("enums", "constants"):
                        out[key] = [n for n in out[key] if n != name]
                    continue
                else:
                    continue
                if side == "added" and short not in names:
                    names.append(short)
                elif side == "removed" and short in names:
                    names.remove(short)
    out["namespaces"] = {ns: names for ns, names in out["namespaces"].items() if names}
    out["carried"] = to
    return out


def build(codex, version, scan=True, carry_scan=False):
    docs = json.loads((codex / "api" / "apidocs.json").read_text(encoding="utf-8"))
    usage = [line.strip() for line in (codex / "api" / "api_usage.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    man, client, protected = manual(codex, docs)
    cpath = codex / "api" / "changes.json"
    changes = json.loads(cpath.read_text(encoding="utf-8")) if cpath.is_file() else []
    for record in changes:                         # the notes in English as well (the App's English pages)
        for item in record.get("added", []) + record.get("changed", []):
            if item.get("note"):
                item["note_en"] = note_en(item["note"])
    pack = dict(pack="api", version=version, client=client, interface=16001, built=time.strftime("%Y-%m-%d"),
                source=SOURCE, namespaces=docs["namespaces"], usage=usage, manual=man, changes=changes,
                protected_globals=protected)
    if scan:
        game = game_scan(codex, client, older=carry_scan)
        if game.get("client") != client:
            game = carry(game, changes, docs, client)
        pack["runtime"] = runtime(game, docs)
        if game.get("carried"):
            pack["runtime"]["carried"] = game["carried"]
    return pack


def game_scan(codex, client, older=False):
    """the game scan beside the documentation (api/game_scan.json); SystemExit when there is none, or it is of another
    client build than the documentation (older: another build is taken, for carry())"""
    spath = codex / "api" / SCAN
    if not spath.is_file():
        raise SystemExit(f"{spath}: no game scan; in the game of {client}: wuxian api-scan --out {spath} (or --no-scan)")
    game = json.loads(spath.read_text(encoding="utf-8"))
    if game.get("client") != client and not older:
        raise SystemExit(f"{spath} is a scan of {game.get('client')}, the documentation is of {client}: scan the game "
                         f"again (wuxian api-scan --out {spath}), or --no-scan / --carry-scan")
    return game


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--codex", default=os.environ.get("WUXIAN_CODEX"), required=not os.environ.get("WUXIAN_CODEX"),
                    help="the checkout of the wuxianwow.com data repository (or WUXIAN_CODEX)")
    ap.add_argument("--version", default=time.strftime("%Y.%m.%d") + ".1", help="the pack's version (default: today.1)")
    how = ap.add_mutually_exclusive_group()
    how.add_argument("--no-scan", action="store_true", help=f"without the game scan (api/{SCAN}): the documentation alone")
    how.add_argument("--carry-scan", action="store_true", help=f"a scan of an earlier build (api/{SCAN}), brought to the "
                     "documentation's build by its changes.json")
    args = ap.parse_args()
    pack = build(Path(args.codex), args.version, scan=not args.no_scan, carry_scan=args.carry_scan)
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
    game = pack.get("runtime")
    if game:
        documented = {(n["ns"], f["name"]) for n in pack["namespaces"] if n["ns"].startswith("C_") for f in n["functions"]}
        scanned = {(ns, f) for ns, names in game["namespaces"].items() for f in names}
        print(f"game scan of {game['client']} ({game['scanned']})"
              + (f", carried to {game['carried']} by the documentation's changes" if game.get("carried") else "")
              + f": {len(scanned - documented)} C_ functions the "
              f"documentation leaves out, {len(documented - scanned)} documented ones the game lacks; "
              f"{len(game['globals'])} global functions, {len(game['enums'])} Enum and {len(game['constants'])} Constants "
              f"tables ({len(game['fields'])} with fields from the game)")


if __name__ == "__main__":
    main()
