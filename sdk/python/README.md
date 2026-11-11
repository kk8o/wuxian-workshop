# WuxianKit Python SDK

`wuxiankit.py`：给本机 AI 应用用的 WuxianKit 客户端，也是写本机扩展的库。单文件，只用标准库，Python 3.10 起。

它经无限工坊的守护进程连到游戏里的 WuxianKit：调用能力、合成提案并等结果、读事件；`Client.open` 启动一个声明过的应用，`Client.app` 加入一个在运行的应用（WuxianKit `ARCHITECTURE.md` 第 10 节），逐个交出唤醒。心跳、重载后重读状态、守护进程重启后续上、断线退避、按游标补读都由它做。

## 放进项目

从无限工坊仓库的 `sdk/python/wuxiankit.py` 复制一份到自己的项目，直接 `import wuxiankit`。MIT 许可，文件头里有许可证全文。本机扩展不用复制：工坊启动它时把这个文件所在的文件夹放进 `PYTHONPATH`。

前提：无限工坊在运行，游戏里装了 WuxianKit 0.6 或更新版本。守护进程的地址和令牌在 `%WUXIAN_HOME%\state\daemon.json`，没设 `WUXIAN_HOME` 时在 `%LOCALAPPDATA%\WuxianWorkshop\state\daemon.json`；`Client("路径")` 可以另指。

## 基本用法

```python
from wuxiankit import Client, WuxianError

kit = Client()
me = kit.call("sense.character")                       # 读取类：直接返回结果
p = kit.call("chat.send", {"channel": "guild", "text": "晚上好", "_title": "打个招呼"})
p = kit.propose([("tune.cvar.set", {"name": "autoLootDefault", "value": "1"}),
                 ("tune.binding.set", {"key": "CTRL-M", "value": "TOGGLEWORLDMAP"})], title="两项设置")
end = kit.wait(p["id"], timeout=120)                   # done / declined / expired / failed / undone；超时带 timeout=True
ans = kit.events(["kit.proposal", "kit.ready"], since=-1, wait=25)   # {events, next, truncated, session}
```

出错是 `WuxianError(reason, code, status)`：`code` 是守护进程的错误码（`link_down` 游戏没连上、`call_failed` 能力拒绝了、`bad_request`……），或客户端自己的 `no_daemon`（无限工坊没开）、`timeout`、`bad_answer`、`no_proposal`、`not_started`（应用没能启动）。

## 应用：声明、启动、加入

三件事分开：

- 声明：应用是什么（标题、听哪些来源、节奏、控制、技能）。本机扩展写在清单的 `apps` 里，由工坊替它声明；插件用 `WuxianKit:App`；独立的程序自己调一次 `kit.call("kit.app.declare", {"app": ..., ...})`。声明是整份的：没给的字段取默认值。
- 启动：`kit.open(id, minutes=None, lease=None)`，回状态 `{on, ends, lease, next, ...}`。在运行的续上。玩家挡住了等启动不了的情形抛 `WuxianError`，`code` 是 `not_started`；没有这个应用是 `call_failed`。
- 加入：`kit.app(id, since=None, wait=None, yield_timeouts=False)`。只加入，不声明也不启动：应用没在运行，流立刻结束，`app.ended` 是原因。

游标是 WuxianKit 的条目编号。`since=N` 从条目 N 之后接着听：应用在这期间发过的唤醒里的条目先合成一个 `reason` 为 `catchup` 的唤醒交出；不给就从应用上一次唤醒结束的地方开始，听到了还在攒的条目跟它的下一次唤醒来。`app.cursor` 是已经交出或越过的最后一个条目的编号：存下来，下次启动传给 `since`，中间的不丢也不重复。

## 陪玩式：长时间运行的本机进程

听游戏动态，按玩家在游戏里下的指令调整，自己决定说什么：

```python
from wuxiankit import Client

kit = Client()
kit.call("kit.app.declare", {"app": "companion", "title": "无限陪玩", "hear": ["game:*"], "me": True,
                             "controls": ["mute", "unmute", "less", "more"], "lease": 600, "minutes": 720})
kit.open("companion")
app = kit.app("companion", since=load_cursor())        # 上次存的 app.cursor；第一次是 None

muted, chatty = False, 1.0
while app.ended is None:
    wake = app.get(timeout=5)                          # 最多等 5 秒，顺便刷新状态行
    show_status(*app.state)                            # ("connected", "") 或 ("waiting", 原因)
    if wake is None:
        continue
    for item in wake["items"]:
        if item["source"] == "control":                # 玩家在游戏里点的按钮、/wk companion 少说
            cmd = item["cmd"]
            if cmd in ("mute", "unmute"):
                muted = cmd == "mute"
            elif cmd == "less":
                chatty = max(0.25, chatty / 2)
            elif cmd == "more":
                chatty = min(2.0, chatty * 2)
        elif item["source"] == "game" and not muted:   # {kind, topic, data, me}
            speak(director(item["kind"], item["data"], item.get("me"), chatty))
    save_cursor(app.cursor)
print("应用停了：", app.ended)                        # player、left、expired、forgotten……
```

`app` 不会因为无限工坊没开、游戏没连上而抛异常：它按 1、2、5、10 秒退避重试，`app.state` 说明在等什么。玩家停了应用（`/wk` 窗口里的应用卡片、`stop` 指令），`app.ended` 就是原因，循环结束。程序退出时 `app.close(stop=True)` 顺便在游戏里停掉它；`close()` 只是不再听，应用到租期（`lease`）自己停。

陪玩做成本机扩展时不用自己听：应用写进清单的 `apps`，反应快的事交给清单里的反射（`reactions`）和绑定了能力的控制（`controls` 的 `call`），工坊在应用运行期间替它做。见下一节和 `extension_guide`。

## 本机扩展：给无限工坊加能力

本机扩展是游戏外的一个文件夹（清单 `wuxian-extension.json`），和 WuxianKit 在游戏里的扩展同一个模型：能力（读取 `see`、存储 `keep`、指引 `point`）、来源、选项、说明、应用。无限工坊启动它、经 stdin / stdout 和它说话（JSON-RPC，一行一个对象；stderr 是日志），Agent 把它的能力当工具 `wk_<扩展>_<能力>` 用。

```python
from wuxiankit import Extension, ExtensionError

ext = Extension()                                      # id 取 WUXIAN_EXTENSION，标题等取清单
volume = ext.option("volume", kind="number", default=80, min=0, max=100, title="音量")
said = ext.source("said", title="说完一句", doc="Each line spoken: {text, seconds}.")

@ext.point("say", title="说一句", doc="Speaks text aloud; returns {seconds}.", args={"text": "string", "mood": "calm|happy?"})
def say(text, mood="calm"):                            # 参数按名字传；没给的可选参数不传
    if not text.strip():
        raise ExtensionError("text is empty")          # 执行失败（code 1），消息给 Agent 看
    seconds = 0.0
    for part in sentences(text):
        if ext.cancelled():                            # 工坊不等这次调用了：停下
            break
        seconds += speak(part, mood, volume.get())
    said.emit({"text": text, "seconds": seconds})
    return {"seconds": seconds}

ext.run(setup=load_model)                              # setup 在回答 initialize 之前跑；print 自动进 stderr
```

- 声明在导入时就按工坊的规则检查：能力名（小写字母、数字、下划线，可以用点分段，工具名 `wk_<扩展>_<能力>` 不超过 48 个字符）、参数写法、超时、选项。不合规是 `ValueError`。检查的代码是工坊 `local/validate.py` 的副本，夹在文件里两行 `shared` 标记之间。
- 能力在线程里并发跑，同时最多 8 个（`Extension(workers=...)`），再来的调用答「忙」（code 2，`busy`）；扩展内部自己加锁。
- `ext.caller`：能力里看谁在调，`{by, id?, app?, chain?}`。`by` 是 `agent`（工具调用）、`extension`（另一个本机扩展 `id`）、`workshop`（反射，或应用 `app` 的唤醒附注）、`player`（玩家按了应用 `app` 的控制）。
- `ext.cancelled()`：工坊不再等这次调用（超时了，或问的人走了）时为真；长活在步骤之间问一下。`ext.remaining()`：工坊还等多少秒，不知道时是 `None`。
- `ext.call("sense.bags")` 经工坊调别的能力（读取、存储、指引三类），失败抛 `ExtensionError`：`.code`、`.message`、`.reason`（协议的一个词：`timeout`、`down`、`starting`、`failed`、`bad_args`、`no_capability`、`busy`、`paused`、`cancelled`、`not_allowed`、`needs_approval`、`loop`）。
- `ext.on_options(fn)` 在玩家改了选项后调用。
- `ext.data`：扩展自己存东西的文件夹（`Path`，用到时建）。工坊在环境变量 `WUXIAN_EXTENSION_DATA` 里给它（`<工坊目录>\state\extensions\<扩展>\data`），在扩展的文件夹之外：更新扩展不丢，打扩展包也不带上。手动运行、没有这个变量时按 `WUXIAN_HOME` 算出同一个位置；测试里把 `WUXIAN_EXTENSION_DATA` 指到临时文件夹。
- 来源只有反射听得到（清单里应用自己的，或 Agent 用 `reaction_set` 设的）；应用的 `hear` 只收游戏里的来源。

测试用 `ExtensionTest(ext, setup=..., options=..., answers=...)`：不起进程，在内存里按协议 `initialize`、`call`（可以带 `caller=`、`deadline_ms=`）、`ping`，收 `event`，给扩展发出的 `call` 回假答；`start` / `cancel` / `result` 测取消，`check_description()` 给出工坊会挑的毛病。

完整的标准（清单的 `apps`、`adds`、`pass`，批准，协议，开发循环，规矩）是无限工坊的 `extension_guide`（仓库里 `src/wuxianworkshop/data/local-extension.md`）。

## Agent 式：听到就想，想好就提案

```python
kit.open("guild")                                      # 应用由 WuxianKit 自带，或由某个扩展的清单声明
app = kit.app("guild")

for wake in app:                                       # 一直等到下一个唤醒；应用停了循环就结束
    facts = (wake.get("notes") or {}).get("facts")     # 本机扩展 facts 给这次唤醒加的附注（没有就是 None）
    for item in wake["items"]:
        if item["source"] == "player":                 # 玩家 /wk guild <一句话>
            note(item["text"])
        elif item["source"] == "chat" and item.get("why") in ("mention", "question") and not item.get("self"):
            text = think(item["plain"], item["sender"], facts)
            if text:
                p = kit.call("chat.send", {"channel": "guild", "text": text, "reply": item["id"], "stale": 3})
                print(item["id"], kit.wait(p["id"])["state"])
```

说话、改游戏只经发送和更改类的提案：玩家点「执行」，或玩家自己勾了免确认的类别。`kit.wait` 等提案时不发心跳，最长等的时间要比租期的三分之一短（默认租期 600 秒、心跳 200 秒一次）。

`kit.app(..., wait=60)`：每次 `next` 最多等 60 秒，没等到循环就停（应用照常在跑，再迭代就接着等）；加 `yield_timeouts=True` 则交出 `None`。

## 唤醒与条目

唤醒（事件 `app.<id>`）：`{app, seq, reason, items, skipped, after, next, state?, pace?, quiet?, notes?}`。

- `reason`：`item`（不汇总，到了就发）、`urgent`、`full`、`batch`、`idle`，以及补读时合成的 `catchup`。
- `after`、`next`：这次唤醒覆盖编号在 `after` 之后、到 `next` 为止的条目；`seq` 是应用这次运行的第几个唤醒。
- `skipped`：单个事件太大时去掉的条目数，可以用 `kit.call("kit.app.recent", {"app": ..., "since": id})` 回看。
- `notes`：`{<扩展 id>: 结果}`，本机扩展用清单的 `adds` 给这个应用的唤醒加的附注；失败的那一份是 `{error, reason}`，唤醒照常到。
- 已经交出过的条目（编号不大于游标）不再交出，全部重复的唤醒整个丢掉；流发现中间缺了（唤醒的 `after` 比游标大、守护进程重启、事件环丢了事件）就用 `kit.app.recent` 补读到应用的唤醒到过的地方，一次最多 100 条。

条目都带 `id`、`time`、`source`：

| source | 字段 |
|---|---|
| `chat` | 聊天消息的全部字段（`channel`、`text`、`plain`、`sender`、`self`、`mention`、`why`……）；`id` 用作回复的 `reply` |
| `game` | `kind`、`topic`、`data`、`me?` |
| `bus` | `topic`、`data` |
| `addon` | `data` |
| `control` | `cmd` |
| `player` | `text` |

声明的字段（`hear`、`digest`、`pace`、`urgent`、`controls`、`say`、`me`、`lease`、`minutes`、`options`、`prompt`）、核心的三档节奏、`kit.app` 事件与 `why` 的取值见 WuxianKit `ARCHITECTURE.md` 第 10 节。

## 测试

工坊仓库的 `tests/sdk/`：`test_extension.py` 本机扩展（内存里与真子进程）；`test_protocol.py` 协议第 1 版（版本协商、`cancel`、`ping`、`busy`、`reason`）；`test_sdk.py` 客户端与应用流，假的守护进程（`http.server`）加可注入的时钟，`Client(clock=..., sleep=...)`，不用真等；`test_validate_sync.py` 核对文件里那段共用检查和工坊的 `local/validate.py` 一字不差。改了 `local/validate.py` 跑 `python scripts/sync_sdk_validate.py` 同步进来，`--check` 只核对。
