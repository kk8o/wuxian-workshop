r"""新建插件 (the 新建插件 page, `wuxian new`, the MCP tool new_addon): a folder in Interface\AddOns with its .toc, the main
Lua file and the notes for the developer's agent (AGENTS.md, which CLAUDE.md imports), from TEMPLATES. The Lua registers
the addon's namespace for hot loading (WoWBridgeNS) and carries its state across a hot reload (OnUnload / OnReload),
so `load` works on it from the first minute; the game itself only finds a new folder when it starts.

create(addons, name, ...) -> dict(name, path, files, template, slash, restart): restart is True because the running
game does not know the folder yet (hot loading works meanwhile).
"""
import re
from pathlib import Path

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,39}$")
TEMPLATES = {"basic": "斜杠命令 + 存档变量", "window": "斜杠命令 + 存档变量 + 一个可拖动的窗口"}
INTERFACE = 16001                                 # 1.60.1: what a new addon gets when the client's is not known


class ScaffoldError(ValueError):
    pass


TOC = """## Interface: {interface}
## Title: {title}
## Notes: {notes}
## Author: {author}
## Version: 0.1.0
## SavedVariables: {name}DB
## X-Made-With: 无限工坊

{name}.lua
"""

LUA_HEAD = """-- {title}{notes_line}
-- 用无限工坊开发：AGENTS.md 写了 Agent 怎么热加载、看报错、截图。
local addonName, ns = ...
WoWBridgeNS = WoWBridgeNS or {{}}                -- 让无限工坊热加载时把这个插件自己的命名空间交给文件
WoWBridgeNS[addonName] = ns

local function Print(...)
\tprint("|cffd8a85a" .. addonName .. "|r", ...)
end
ns.Print = Print
"""

LUA_EVENTS = """
-- 事件：热加载会把文件再跑一遍，所以事件框体只建一次，留在 ns 里
local events = ns.events or CreateFrame("Frame")
ns.events = events
events:UnregisterAllEvents()
events:RegisterEvent("ADDON_LOADED")
events:RegisterEvent("PLAYER_LOGIN")
events:SetScript("OnEvent", function(_, event, arg1)
\tif event == "ADDON_LOADED" and arg1 == addonName then
\t\t{name}DB = {name}DB or {{ logins = 0 }}         -- 存档变量：登出或 /reload 时写盘
\t\tns.db = {name}DB
\telseif event == "PLAYER_LOGIN" and ns.db then
\t\tns.db.logins = ns.db.logins + 1
\t\tPrint(("已加载（第 %d 次登录），输入 /{slash} 试试"):format(ns.db.logins))
\tend
end)
"""

LUA_WINDOW = """
-- 窗口：热加载前 OnUnload 把旧的藏起来，再跑一遍文件时按新代码重建
local function MakeWindow()
\tlocal w = CreateFrame("Frame", nil, UIParent, "BasicFrameTemplateWithInset")
\tw:SetSize(320, 180)
\tw:SetPoint("CENTER")
\tw:SetMovable(true)
\tw:EnableMouse(true)
\tw:RegisterForDrag("LeftButton")
\tw:SetScript("OnDragStart", w.StartMoving)
\tw:SetScript("OnDragStop", w.StopMovingOrSizing)
\tw:SetClampedToScreen(true)
\tlocal title = w.TitleText or w:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
\ttitle:SetPoint("TOP", w, "TOP", 0, -6)
\ttitle:SetText("{title}")
\tlocal text = w:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
\ttext:SetPoint("CENTER", w, "CENTER", 0, -8)
\ttext:SetText("你好，艾泽拉斯！")
\tw.text = text
\tw:Hide()
\treturn w
end
ns.window = MakeWindow()
"""

LUA_SLASH_BASIC = """
-- 斜杠命令 /{slash}
SLASH_{upper}1 = "/{slash}"
SlashCmdList["{upper}"] = function(msg)
\tPrint("你好！" .. (msg ~= "" and ("参数：" .. msg) or ""))
end
"""

LUA_SLASH_WINDOW = """
-- 斜杠命令 /{slash}：开关窗口
SLASH_{upper}1 = "/{slash}"
SlashCmdList["{upper}"] = function()
\tns.window:SetShown(not ns.window:IsShown())
end
"""

LUA_RELOAD = """
-- 热加载：无限工坊再跑这个文件之前调用 OnUnload，跑完调用 OnReload，并把 OnUnload 的返回值交给它
function ns.OnUnload()
{unload}\treturn {{ db = ns.db }}
end

function ns.OnReload(state)
\tns.db = (state and state.db) or {name}DB
\tPrint("已热加载")
end
"""


def lua_source(name, title, notes, template):
    """the main Lua file of a template"""
    v = dict(name=name, title=title.replace('"', "'"), slash=name.lower(), upper=name.upper(),
             notes_line=f"：{notes}" if notes else "")
    parts = [LUA_HEAD.format(**v), LUA_EVENTS.format(**v)]
    if template == "window":
        parts += [LUA_WINDOW.format(**v), LUA_SLASH_WINDOW.format(**v)]
        unload = "\tif ns.window then ns.window:Hide() end\n"
    else:
        parts.append(LUA_SLASH_BASIC.format(**v))
        unload = ""
    parts.append(LUA_RELOAD.format(unload=unload, **v))
    return "".join(parts)


AGENTS = """# {title}（{name}）· 给 Agent 的说明

这是《魔兽世界》国服「无限」客户端的插件，用「无限工坊」开发。无限工坊的 MCP 服务器 `wuxian`（命令行同名：`wuxian`）
连着正在运行的游戏，让你自己写插件、自己调试：改完的代码直接装进游戏试（热加载，不用 /reload），读报错和 print 输出，
在游戏里执行 Lua 查状态，录事件、查界面、截图，对照这个客户端自己的 API 手册改好再试。

## 客户端

- {client_line}`## Interface: {interface}`；界面代码是正式服 Mainline（12.x），游戏类型 camelot。游戏大版本更新时 Interface 号会变，
  toc 里的号对不上，游戏就把插件当成过期插件不加载（玩家勾选「加载过期插件」除外）：`check` 会提醒。
- Lua 5.1.4：没有 `io`、`os`、`require`、`loadfile`、文件和网络访问；全局表 `_G`。文件用 UTF-8。
- 新文件、改 .toc：游戏只在启动时读 toc、发现文件，要完整退出游戏再启动（/reload 不够）。热加载不受这个限制。

## 开发循环

1. `status`：确认 `link.state` 是 `online`（游戏在跑、开发组件 WoWBridge 连着）。
2. 改 `{name}.lua`（新文件记得写进 `{name}.toc`）。
3. `check`，target `{name}`：不进游戏就找出会出错的地方——Lua 5.1 语法（客户端就是 5.1：没有 `//`、`goto`、位运算符）、
   这个客户端没有的库和函数（`os`、`io`、`utf8`、`require`、`table.unpack`…）、拼错的全局名和 API、忘了 `local` 的全局变量、
   受保护函数、受限事件、toc 与 XML；手册里查不到的名字，游戏在线时会直接问游戏。先把 `errors` 改掉，`warnings` 逐条看。
4. `load`，target `{name}`：按 toc 顺序把插件的 Lua 全部热加载进游戏，返回每个文件的结果。`load` 和 `watch` 自己也会查
   语法，有语法错误的文件不会送进游戏（`check_failed` 带文件和行号）。
5. `try` 把功能走一遍：执行斜杠命令（`slash`，例如 `/{slash} show`）或一段代码，一次带回结果和之后几秒的报错（带调用栈）、
   `print`、被拦截的动作，需要时加 `events` 和截图；`ok` 为真才算这一步过了。也可以分开看：`logs`（kinds `ERR,OUT`）、
   `run` 查状态（例如 `return {name}DB`）、`snap` 截图、`trace` 录事件、`inspect` 查框体。重复 2–5。
6. 要真正重载界面才生效的（.toc、存档变量写盘、XML、只在登录时跑的代码）：`reload`，玩家点游戏里的按钮才会重载。
7. 用户问「游戏里能不能用」时，先查手册（下一节），再用 `run` 实测，不要只凭记忆。

## 不用鼠标键盘也能把功能走一遍

- `try` 一次做完：`slash` 给一行斜杠命令（`/{slash} show`），或 `code` 给一段代码（`MyButton:Click()`），回来的是这一步的
  结果、之后 `seconds` 秒内的报错（带调用栈和来自哪个插件）、`print`、警告、被拦截的动作，加 `events` 录事件，加 `snap` 或
  `frame` 截图。改完一个 bug，用同一个 `try` 再走一遍，看报错是不是没了。玩家自己在游戏里的操作也会出现在里面，看 `addon`。
- 斜杠命令也可以 `run` `SlashCmdList["命令名"]("参数")`（命令名是 `SLASH_命令名1` 里的那个）。
- 按钮：`run` `你的按钮:Click()`；事件处理函数：用假参数直接调用，例如 `ns.events:GetScript("OnEvent")(ns.events, "BAG_UPDATE", 0)`。
  只对你自己的框体这样做；受保护的动作（施法、选目标等）插件本来就做不了。
- 不知道该监听哪个事件、参数长什么样：`trace` 录一段时间（让用户在游戏里做那个动作，或者你 `run` 一段代码），只想看
  某一类就给 `events`，例如 `BAG_*, LOOT_*`。
- 界面位置、大小、显隐不对：`inspect` 给一个框体（`MyAddonFrame`、`ns.window`），看它的矩形、锚点、层级和子框体；
  用户说「这里不对」时请他把鼠标放上去，用 `mouse=true`；加 `snap=true` 只截那一块。

## 改坏了能退回去

- 每次 `load`、开始 `watch`、`watch` 检测到保存并热加载之前，无限工坊都会给插件存一份（文件没变就不重复存）。
- `history` 列出存过的版本（编号、时间、为什么存、改了哪些文件）；带 `id` 看那一版和现在的差异，`against="prev"`
  看那一版自己改了什么。
- `restore` 把插件的文件回退到某一版；回退前的文件会先存一份，所以回退也能撤销。回退后 `load` 一次（或 `reload`），
  游戏里跑的才是回退后的代码。
- 改不是你在这次对话里新建的插件（用户自己的、别人写的）之前，先 `checkpoint` 存一份原样，写一句说明。

## 查 API

无限工坊内置这个客户端自己的 API 手册（MCP 工具，不用开游戏）：

- `api_search`：按名字或说明里的词找函数、事件、枚举与结构，带签名和标记（受保护、可能返回机密值等）。
- `api_get`：一个条目的全部信息：参数、返回值、能否为空、原始文档字段。
- `api_manual`：规矩：运行时、taint、机密值、.toc、受保护函数、GameRule、无限专属 API。

手册来自客户端自己的文档，正式服的资料不一定适用这里；拿不准时在游戏里实测：`run` `return type(C_Foo.Bar)`。

## 热加载约定（模板已经写好）

- 文件开头登记命名空间：`local addonName, ns = ...`，`WoWBridgeNS[addonName] = ns`；热加载时文件拿到的是同一个 `ns`。
- 热加载会把文件再跑一遍：事件框体、钩子只建一次（放在 `ns` 里复用，或先 `UnregisterAllEvents`）；`ns.OnUnload()`
  在重跑前收拾旧东西（藏起旧窗口、取消计时器）并返回要保留的状态，`ns.OnReload(state)` 在重跑后接回来。
- `ADDON_LOADED`、`PLAYER_LOGIN` 在热加载时不会再触发：初始化写成函数，登录时和 `OnReload` 里都调用。

## 这个客户端的规矩

- 受保护函数（施法、选目标、移动等）插件不能直接调用；战斗中不能改受保护的框体（动作条、单位框体）。违规会出
  `ADDON_ACTION_BLOCKED` / `FORBIDDEN`，`logs` 里能看到。
- 机密值（secret values，12.0 起）：战斗、首领战、PvP 对局、聊天锁定时，部分 API 返回机密值，可以原样传回暴雪 API，
  但不能比较、运算、拼接或存进表。
- 不要注册 `COMBAT_LOG_EVENT_UNFILTERED` 这类受限事件（API 手册里标「受限」）：客户端会拦截，并弹窗要用户禁用插件。
- 觉得被拦截了、没反应，或者用户说游戏里有弹窗：先看 `logs`（kinds `ERR,BLOCKED`），再 `snap` 截图看游戏里的提示——很多
  警告只以弹窗出现。
- 不要让用户或你自己模拟按键、读游戏内存、注入代码；只用插件 API。

## 文件

- `{name}.toc`：插件信息和文件列表。
- `{name}.lua`：主文件（模板：{template_text}）。
- `AGENTS.md`：这份说明；`CLAUDE.md` 引用它。
"""


def create(addons, name, title=None, notes="", template="basic", interface=INTERFACE, author="", client=None):
    """makes the addon's folder; ScaffoldError for a bad name, an unknown template or a folder that exists"""
    addons = Path(addons)
    if not NAME.match(name or ""):
        raise ScaffoldError("插件名只能用英文字母、数字和下划线，字母开头，2–40 个字符（它也是文件夹名）")
    if template not in TEMPLATES:
        raise ScaffoldError(f"没有这个模板：{template}（可选：{'、'.join(TEMPLATES)}）")
    if not addons.is_dir():
        raise ScaffoldError(f"插件目录不存在：{addons}")
    folder = addons / name
    if folder.exists() or any(p.name.lower() == name.lower() for p in addons.iterdir()):
        raise ScaffoldError(f"已经有叫 {name} 的插件了")
    title = (title or name).strip() or name
    notes = " ".join((notes or "").split())
    files = {
        f"{name}.toc": TOC.format(interface=interface, title=title, notes=notes, author=author or "", name=name),
        f"{name}.lua": lua_source(name, title, notes, template),
        "AGENTS.md": AGENTS.format(title=title, name=name, slash=name.lower(), template_text=TEMPLATES[template],
                                   interface=interface, client_line=f"{client}，" if client else ""),
        "CLAUDE.md": "@AGENTS.md\n",
    }
    folder.mkdir()
    for fname, text in files.items():
        (folder / fname).write_text(text, encoding="utf-8", newline="\n")
    return dict(name=name, path=str(folder), files=list(files), template=template, slash=f"/{name.lower()}", restart=True)
