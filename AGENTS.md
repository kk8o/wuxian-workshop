# AGENTS.md — 给编码 Agent 的说明

## 这是什么
- 无限工坊（Wuxian Workshop）：用 AI 写魔兽插件。魔兽世界插件（`addon/`）与同机守护进程（`src/wuxianworkshop/`）之间的双向通道，外加给开发者 Agent 用的本地接口（HTTP + MCP）、命令行 `wuxian`、桌面窗口与安装器；给有一点编程基础、配合 AI Agent 做插件的人。
- 插件在游戏窗口左上角（玩家可以拖到别处）画彩色单元帧，守护进程抓屏、定位、解码；反向用"字体信箱"把字节写进字体文件的字形宽度，插件加载字体后测量读回。
- 不读内存、不注入代码、不模拟输入；守护进程只抓屏和读写插件目录里的文件。重载 UI 只能由玩家点插件弹出的按钮。

## 目录
- `src/wuxianworkshop/`：产品 Python 包（src 布局，包名 `wuxianworkshop`，命令行 `wuxian`）。
- `core/`：帧格式 v1（`frame.py`，格式的参考实现）、定位（`locate.py`）、解码（`decode.py`）、字体包（`fontpack.py` → 零依赖 TTF 写入器 `ttf.py`）、信箱（`mailbox.py`）、游戏目录、`.build.info` 与原子写（`game.py`）、SavedVariables 读取器（`savedvars.py`：只解析字面量表，绝不执行）。
- `transport/`：抓屏（`capture.py`，GDI 与纯 ctypes 的 WGC；找游戏窗口按 exe 名，或按窗口类加客户端目录）、链路协议（`link.py`：握手、心跳、累计确认、分片、ping、首字节分型的上行消息、跳槽重发；0.9.6 起 HELLO / PONG 带 `f=1` 的插件改为懒确认（约 10 秒一包，省信箱槽位）、缺了哪条立刻用 PARTS 要、小消息合批成 TYPE_BATCH，run 结果优先上屏；守护进程知道的会话、日志、写过的槽位都有上限）。
- `agent/`：命令与热加载（`commands.py` 的 `AgentCommands` mixin，`link.Companion` 继承它）、截图（`snap.py`）、debug.log（`debuglog.py`）、帧监视器（`monitor.py`）、探测帧解析（`diag.py`）、送进游戏前的检查（`lint.py`：lupa 的 Lua 5.1 编译查语法；解析 string.dump 的字节码拿到全局读写和行号，与 API 手册、标准库、常用暴雪界面全局名比对；toc 与 XML；查不定的名字在线时问游戏）、插件的历史版本（`history.py`）、事件追踪与界面检查（`probes.py`，经 `run` 送进游戏的 Lua，答 JSON；也生成调用插件公开函数、描述插件接口的片段）。
- `daemon/`：`server.py`（Starlette + uvicorn，127.0.0.1 随机端口 + Bearer token，`state\daemon.json`，单实例互斥量，`/mcp` 挂载）、`service.py`（操作层，HTTP 与 MCP 共用；`try` 在这里：动作、等待、标记 run 收齐）、`journal.py`（环形日志 + SSE 扇出；插件用 WoWBridge 的 Emit 发的事件也进这里，类型 EVENT，单独一个环，JSON 严格读）、`api.py`（`ApiError`、RUN 文本解析、读 daemon.json）、`companion.py`（`CompanionLoop`：找窗口、抓帧、写信箱，跟着正在运行的游戏的目录走）。
- `cli/`：`main.py`（`wuxian` 入口，全部子命令）、`client.py`（urllib 客户端，守护进程没起就拉起它）、`mcpconfig.py`（`wuxian mcp-config` 片段）。
- `mcp/server.py`：MCP 工具（status / check / try / run / load / watch / snap / reload / logs / history / checkpoint / restore / trace / inspect / events / call / respond / addon_api / addons / errors / install / new_addon / API 手册的 api_search / api_get / api_manual）；挂在守护进程 `/mcp`，也可 `wuxian mcp` 走 stdio（stdout 只放协议）；stdio 进程只跑自己启动时的代码，它的 `status` 带 `mcp`（`stale`：比程序旧，或源码在它启动后改过，要在 Agent 里重连）。`mcp/kit.py`：扩展框架 WuxianKit 的 `wk_` 工具、资源与提示词，按游戏里的清单生成，清单缓存在 `state\kit-manifest.json`。`mcp/changes.py`：这些列表变了告诉客户端，2026-07-28 协议走订阅总线（subscriptions/listen），握手时代的连接发 list_changed 通知。
- `installer/`：`install.py`（先清后装；开发者模式装两个插件与信箱；.toc 写入客户端的 Interface 号；删旧版本与早期实验的残留；绝不碰 `WoWBridge\mail\`；`restart_for_link` 区分"新增文件、链路要等游戏重启"和只改 toc）、`doctor.py`（自检与修复）、`addons.py`（已装插件清单；客户端的 Interface 号：游戏报告的 > 已验证的 > 按版本号推算）、`runtimes.py`（WebView2、.NET Framework、WGC 所需的系统版本）、`__init__.py`（`addon_source_dir()`：开发时仓库 `addon/`，冻结时 `_internal\addon\`）。
- `ui/`：`static/`（index.html、i18n.js、app.js、app.css、htmx、Alpine，零外链，由守护进程在 `/` 提供）、`shell.py`（pywebview 壳）、`tray.py`、`webview2.py`、`icon.py`。
- `updater.py`：程序更新（Velopack，源在 wuxianwow.com/workshop/releases）；`content.py`：内容包（程序自带一份，网站上有新的就下载）；`apidocs.py`：读 API 手册内容包；`scaffold.py`：新建插件（模板 + AGENTS.md）；`agents.py`：一键接入 Agent（Claude Code 经它自己的命令行，Codex、Cursor、Trae（国内版 Trae CN 和国际版）、WorkBuddy（中文版和国际版）直接改设置文件，先备份）；`data/`：程序自带的内容包；`paths.py`：运行时目录。
- `addon/!WuxianWorkshop/`：平台插件（名字排最前）：早期错误钩子 + 错误收集写 `WuxianWorkshopDB`，`/wxw report on|off|status`、`/wxw clear`。
- `addon/WoWBridge/`：开发组件：Config、Locale（全部文案的中英两份，`ns.L`）、Debug、Codec、Frame（含帧码位置与拖动框）、FontProbe、Mailbox、Agent（热加载、Dump、reset 钩子）、UI（对话框、重载按钮）、Link（传输）、API（给别的插件用：`WoWBridge.Bind(插件名)` 拿句柄，`Emit` 发事件、`Expose` 公开函数、`Request` 向 Agent 提问；守护进程经 `run` 调 `Call`、`Describe`、`Reply`）、Json（编码）、WoWBridge（斜杠命令、设置、链路开关、语言）、Panel（面板：概览、设置、诊断）、Minimap（小地图按钮）、Skin（样式）。
- `tests/`：按 `core/`、`transport/`、`addon/`、`agent/`、`daemon/`、`installer/`、`ui/` 分目录（每级都要有 `__init__.py`）；`tests/support/` 是合成屏幕（`render.py`）、Lua 模拟客户端（`wowmock.py`）、探测帧分析（`probe_report.py`）、fontTools 参考实现（`fontref.py`）和 `fixtures/`。
- `scripts/`：`build.ps1` + `wuxian.spec` + `wuxian_entry.py` + `version_info.py`（PyInstaller onedir → `dist\wuxian\`）、`pack.ps1` + `release_manifest.py`（Velopack 发布 + 网站下载页用的 latest.json）、`set_version.py`（程序与插件同一个版本号）、`make_icons.py`（图标与插件贴图，用系统 Edge 渲染）、`build_api_pack.py`（从无限图鉴的数据生成 API 手册内容包；数据不在本仓库）、`dev_fake_api.py`（界面开发用的假守护进程）。
- `notes/`：各版本的更新说明。

## 命令
- 虚拟环境在仓库的 `.venv`（Python 3.14），Python 命令一律用 `.venv\Scripts\python.exe`。
- 装包（开发模式）：`.venv\Scripts\python.exe -m pip install -e ".[test,ui,mcp,build]"`。
- 跑测试：`.venv\Scripts\python.exe -m unittest discover -s tests -t .`（含用 lupa 跑插件 Lua 的测试和起真实 uvicorn 的守护进程测试，几分钟）。
- 装插件：`.venv\Scripts\wuxian install [--game "<客户端目录>"] [--no-clean]`；新增/删除文件后要完整重启游戏（toc 和新文件只在启动时被发现），只改已有 .lua 则 /reload 即可。
- 自检：`.venv\Scripts\wuxian doctor [--fix]`。
- 守护进程：`.venv\Scripts\wuxian serve [--capture gdi|wgc]`（无窗口）；客户端子命令 `status | check | try | run <lua> | load <target> | watch | snap | reload | logs | events | call | respond | addon-api | say | quit` 和 `wuxian mcp` 在没有守护进程时自动拉起 `wuxian app --background`（测试或无界面环境设 `WUXIAN_HEADLESS=1` 改拉 `serve`）。`wuxian mcp-config claude|codex|cursor` 打印接入片段。
- 窗口：`.venv\Scripts\wuxian`（无参数）= 守护进程 + 托盘 + pywebview 窗口。
- 打包：`powershell scripts\build.ps1`（`dist\wuxian\wuxian.exe`）；发布包：`powershell scripts\pack.ps1 [-Version X] [-Notes notes.md]`（`dist\releases\`）。
- 测试实例：`WUXIAN_HOME`（运行时目录）+ `WUXIAN_MUTEX`（单实例互斥量）+ `serve --mode player`（不读屏幕、不写信箱）可以在正在用的程序旁边再起一个；`WUXIAN_UPDATE_FEED` / `WUXIAN_CONTENT_FEED` 指向本地文件夹测更新和内容包。

## 运行时文件
- 都在 `%LOCALAPPDATA%\WuxianWorkshop\` 下（环境变量 `WUXIAN_HOME` 可改根目录；`tests/__init__.py` 给整个测试默认一个临时目录，测试绝不碰用户的文件）：`state\daemon.json`（端口、token、pid）、`state\settings.json`、`state\install.json`、`state\clients.json`（游戏报告的 Interface 号）、`state\kit-manifest.json`（扩展框架的清单）、`logs\debug.log`、`snaps\*.png`、`history\`、`webview\`。

## 约定
- 注释密度与现有代码一致：模块顶部一段说明用途与协议，函数一行 docstring，只在"为什么"不显然处加行内注释；注释一律英文。界面文案中英两份：页面的中文直接写在 index.html / app.js 里（它也是词条的键：静态文字由 `x-t` 翻译，表达式里用 `t('…{名}…', {名})` 整句模板，不拼接碎片），英文写进 `ui/static/i18n.js` 的 `EN`（`tests/ui/test_i18n.py` 查每条都有英文）；Python 给人看的文字（自检、Agent、新建插件、托盘与对话框）用 `i18n.tr(中文, 英文)`，跟设置里的语言走（自动 = 跟随 Windows 显示语言）。插件里给玩家看的文字一律放 `Locale.lua`（`!WuxianWorkshop` 放 `Core.lua` 的 `WORDS`），中英两份都写；发给守护进程的内容（调试、RUN/RELOAD 结果、测试消息）保持英文。
- 不改协议格式：帧格式 v1（`core/frame.py` 与 `Codec.lua`/`Frame.lua` 互为镜像）、信箱包格式（`mailbox.py` 与 `Mailbox.lua`）、控制帧与上行消息的格式（`link.py` 与 `Link.lua`；EVENT 消息的 JSON 是 `API.lua` 与 `journal.py`）、CODE 头部（`commands.py` 与 `Agent.lua`）、RUN 结果（`Agent.lua` 与 `daemon/api.py` 的 `parse_run`；0.9.0–0.9.4 的插件还在用旧格式，`parse_run` 要一直读得懂）；改一边必须同时改另一边并补测试。新能力靠协商（控制帧的 `f=`、WELCOME 的字段），旧的一边不认识的字段要能忽略：新程序配旧插件、旧程序配新插件都得能用。
- 不模拟输入、不读游戏内存、不注入代码；只允许抓屏和读写插件目录里的文件。
- 游戏客户端只在启动时发现新文件，运行中新增/删除文件无效；替换运行中的文件用 `core.game.atomic_write`。
- 本地接口只绑 127.0.0.1，每个请求带 Bearer token 并校验 Host/Origin；不开远程端口。
- 程序和两个插件同一个版本号，改版本用 `scripts\set_version.py`（`tests/test_versions.py` 检查）。
- 测试必须全部通过再交付；新功能先补 `tests/`，Lua 改动用 `tests/addon` 的模拟客户端覆盖，接口改动用 `tests/daemon` 的真实服务覆盖。
- 除非明确要求，不要 commit / push。
