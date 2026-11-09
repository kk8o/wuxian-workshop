# WuxianKit Python SDK

`wuxiankit.py`：给本机 AI 应用用的 WuxianKit 客户端，也是写本机扩展的库。单文件，只用标准库，Python 3.10 起。

它经无限工坊的守护进程连到游戏里的 WuxianKit：调用能力、合成提案并等结果、读事件；`Client.app` 按一份声明打开一个应用（WuxianKit `ARCHITECTURE.md` 第 10 节），逐个交出唤醒。心跳、重载后重开、守护进程重启后续上、断线退避、按条目编号去重都由它做。

## 放进项目

从无限工坊仓库的 `sdk/python/wuxiankit.py` 复制一份到自己的项目，直接 `import wuxiankit`。MIT 许可，文件头里有许可证全文。

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

出错是 `WuxianError(reason, code, status)`：`code` 是守护进程的错误码（`link_down` 游戏没连上、`call_failed` 能力拒绝了、`bad_request`……），或客户端自己的 `no_daemon`（无限工坊没开）、`timeout`、`bad_answer`、`no_proposal`。

## 陪玩式：长时间运行的本机进程

听游戏动态，按玩家在游戏里下的指令调整，自己决定说什么：

```python
from wuxiankit import Client

kit = Client()
app = kit.app("companion", title="无限陪玩", hear=["game:*"], me=True,
              controls=["mute", "unmute", "less", "more"], lease=600, minutes=720)

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
print("应用停了：", app.ended)                        # player、left、expired……
```

`app` 不会因为无限工坊没开、游戏没连上而抛异常：它按 1、2、5、10 秒退避重试，`app.state` 说明在等什么。玩家停了应用（`/wk` 的应用页、`stop` 指令），`app.ended` 就是原因，循环结束。程序退出时 `app.close(stop=True)` 顺便在游戏里停掉它；`close()` 只是不再听，应用到租期（`lease`）自己停。

## 本机扩展：给无限工坊加能力

本机扩展是游戏外的一个文件夹（清单 `wuxian-extension.json`），和 WuxianKit 在游戏里的扩展同一个模型：能力（读取 `see`、存储 `keep`、指引 `point`）、来源、选项、说明、应用。无限工坊启动它、经 stdin / stdout 和它说话（JSON-RPC，一行一个对象；stderr 是日志），Agent 把它的能力当工具 `wk_<扩展>_<能力>` 用。工坊启动它时把这个文件所在的文件夹放进 `PYTHONPATH`，不用复制。

```python
from wuxiankit import Extension, ExtensionError

ext = Extension()                                      # id 取 WUXIAN_EXTENSION，标题等取清单
volume = ext.option("volume", kind="number", default=80, min=0, max=100, title="音量")
said = ext.source("said", title="说完一句", doc="Each line spoken: {text, seconds}.")

@ext.point("say", title="说一句", doc="Speaks text aloud; returns {seconds}.", args={"text": "string", "mood": "calm|happy?"})
def say(text, mood="calm"):                            # 参数按名字传；没给的可选参数不传
    if not text.strip():
        raise ExtensionError("text is empty")          # 执行失败（code 1），消息给 Agent 看
    seconds = speak(text, mood, volume.get())
    said.emit({"text": text, "seconds": seconds})
    return {"seconds": seconds}

ext.run(setup=load_model)                              # setup 在回答 initialize 之前跑；print 自动进 stderr
```

能力在线程里并发跑（扩展内部自己加锁）；`ext.call("sense.bags")` 经工坊调别的能力；`ext.on_options(fn)` 在玩家改了选项后调用。测试用 `ExtensionTest(ext, setup=..., options=..., answers=...)`：不起进程，在内存里按协议 `initialize`、`call`，收 `event`，给扩展发出的 `call` 回假答。完整的标准（清单、协议、开发循环、规矩）是无限工坊的 `extension_guide`（仓库里 `src/wuxianworkshop/data/local-extension.md`）。

## Agent 式：听到就想，想好就提案

```python
app = kit.app("guild", title="公会聊天", hear=["chat:guild"], pace=True,
              urgent=["mention", "followup", "question"], controls=["stop"], say=True, prompt="guild-chat")

for wake in app:                                       # 一直等到下一个唤醒；应用停了循环就结束
    for item in wake["items"]:
        if item["source"] == "player":                 # 玩家 /wk guild <一句话>
            note(item["text"])
        elif item["source"] == "chat" and item.get("why") in ("mention", "question") and not item.get("self"):
            text = think(item["plain"], item["sender"])
            if text:
                p = kit.call("chat.send", {"channel": "guild", "text": text, "reply": item["id"], "stale": 3})
                print(item["id"], kit.wait(p["id"])["state"])
```

说话、改游戏只经发送和更改类的提案：玩家点「执行」，或玩家自己勾了免确认的类别。`kit.wait` 等提案时不发心跳，最长等的时间要比租期的三分之一短（默认租期 600 秒、心跳 200 秒一次）。

`kit.app(..., wait=60)`：每次 `next` 最多等 60 秒，没等到循环就停（应用照常在跑，再迭代就接着等）；加 `yield_timeouts=True` 则交出 `None`。

## 唤醒与条目

唤醒（事件 `app.<id>`）：`{app, reason, items, skipped, pace?, quiet?}`。`reason` 是 `item`（不汇总，到了就发）、`urgent`、`full`、`batch`、`idle`；`skipped` 是单个事件太大时去掉的条目数，可以用 `kit.call("kit.app.recent", {"app": ..., "since": id})` 回看。已经交出过的条目（按 `id`，记最近 1000 个）不再交出，全部重复的唤醒整个丢掉。

条目都带 `id`、`time`、`source`：

| source | 字段 |
|---|---|
| `chat` | 聊天消息的全部字段（`channel`、`text`、`plain`、`sender`、`self`、`mention`、`why`……）；`id` 用作回复的 `reply` |
| `game` | `kind`、`topic`、`data`、`me?` |
| `bus` | `topic`、`data` |
| `addon` | `data` |
| `control` | `cmd` |
| `player` | `text` |

声明的字段（`hear`、`digest`、`pace`、`urgent`、`controls`、`say`、`me`、`lease`、`minutes`、`options`、`prompt`）、核心的三档节奏、`kit.app` 事件与 `why` 的取值见 WuxianKit `ARCHITECTURE.md` 第 10 节。`Client.app` 的关键字参数原样放进 `kit.app.open`（值为 `None` 的不发）。

## 测试

工坊仓库的 `tests/sdk/test_extension.py`：本机扩展（内存里与真子进程）。`tests/sdk/test_sdk.py`：假的守护进程（`http.server`）加可注入的时钟，`Client(clock=..., sleep=...)`，不用真等。
