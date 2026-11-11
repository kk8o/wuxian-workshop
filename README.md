# 无限工坊 · Wuxian Workshop

**用 AI 写魔兽插件。**

无限工坊让你的 AI Agent（Claude Code、Codex、Cursor、Trae、WorkBuddy 等支持 MCP 的 Agent）自己写《魔兽世界：无限》插件、自己调试：从零写一个
新插件，或者修改你已经装着的插件（别人写的也行）；写完先检查，再在正在运行的游戏里试，看报错、print 输出和截图，在游戏里执行
Lua 查状态，对照这个客户端自己的 API 手册找到原因、改好再试。你只需说清想要什么，在游戏里验收。

- 官网、下载与图文说明：**https://wuxianwow.com/workshop**
- Windows 10 / 11（64 位）程序，免费，在你自己的电脑上运行。

![无限工坊的开发台：保存后自动热加载，在游戏里试出报错，修好再试](https://wuxianwow.com/art/workshop/app-dev.webp)

## 它怎么工作

| 部分 | 位置 | 做什么 |
| --- | --- | --- |
| 无限工坊 App | `src/wuxianworkshop/` | 本机服务（HTTP + MCP，只监听 127.0.0.1）、桌面窗口与托盘、命令行 `wuxian`、安装与自检 |
| 开发组件 | `addon/` | 游戏里的两个普通插件：`!WuxianWorkshop` 收集所有插件的 Lua 报错，`WoWBridge` 与 App 通信、执行 Agent 送来的代码 |
| 扩展 | `src/wuxianworkshop/local/`、`sdk/python/` | 扩展的登记、检查、批准与扩展包，本机扩展的进程与反射；Python SDK |
| 你的 AI Agent | 你自己的 | 通过 MCP 调用 App：检查代码、热加载、在游戏里试、读日志与截图、查 API 手册 |

App 和游戏之间的通道：

- **游戏 → App**：`WoWBridge` 在游戏画面一角画一小块彩色帧码，App 截取游戏窗口（Windows Graphics Capture）读出来。
- **App → 游戏**：App 把数据写进插件目录里的字体文件（每个字形的宽度就是一个字节），插件加载字体、量出宽度读回来。

不读游戏内存、不注入代码、不模拟按键；重载界面只能由玩家在游戏里点按钮。

## 能做什么

- **写完先检查**：Lua 5.1 语法、这个客户端没有的函数和库、拼错的 API 名字、忘了 `local` 的全局变量、受保护函数与受限事件、
  `.toc` 与 XML；手册里查不定的名字直接问正在运行的游戏。
- **在游戏里试一下**：一次调用执行一条斜杠命令或一段 Lua，拿回结果和之后几秒里的报错（带调用栈）、print 输出、被拦截的操作、
  触发的事件和截图。
- **热加载与监视**：文件保存后自动检查、热加载，不用每次 `/reload`。
- **录事件、查框体**：录一段时间里触发的事件与参数；查框体的位置、锚点、显隐和层级。
- **历史版本**：每次改动前自动存一份，看差异、一键退回。
- **API 手册**：这个客户端自己的函数、事件、枚举与结构，加上规矩（taint、机密值、受保护函数）。
- **游戏更新不影响开发组件**：按客户端当前的 Interface 号安装，游戏大版本更新后自动跟上。

## 扩展

说出一个想法，Agent 先查证能不能做，再在运行中的游戏里做成原型（0.9.13 起）。扩展是一个文件夹加一份清单
`wuxian-extension.json`，按在哪里运行分三种，用同一套工具新建（`new_extension`）、载入（`extension_load`）和调试：

- **游戏内**：带游戏插件（`addons/`），能力经扩展框架 [WuxianKit](https://github.com/kk8o/wuxian-kit) 交给 Agent。
- **本机**：游戏里做不到的事（声音、读写文件、联网、本地模型）写成这台电脑上的一个进程，用 `sdk/python/` 的 Python SDK。
- **游戏 · 本机**：两边各一半。

扩展可以带应用（交给 AI 的一段会话，`wuxian listen <应用>` 接上），也可以设反射：听到某条消息直接调一个能力，不用叫醒 AI。
「打包分享」生成扩展包（zip），对方在 App 的「扩展」页导入。导入的扩展包和新登记的本机扩展，运行前要玩家看过摘要（往游戏里装什么、
在这台电脑上运行什么、Agent 读到什么）再批准，Agent 不能替玩家批准。

案例、扩展包与下载：**https://wuxianwow.com/workshop/extensions**

## 从源码运行

需要 Windows 10 / 11（64 位）、Python 3.14、WebView2 运行时，以及《魔兽世界：无限》客户端。

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[ui,mcp,test]"
.venv\Scripts\python.exe -m unittest discover -s tests -t .    # 全部测试
.venv\Scripts\wuxian install                                     # 把开发组件装进游戏（装完要完整重启游戏）
.venv\Scripts\wuxian                                             # 窗口 + 托盘
.venv\Scripts\wuxian mcp-config claude                           # 接入 Agent 的配置（也有 codex、cursor、trae-cn、trae、workbuddy、workbuddy-ai）
```

打包：`powershell scripts\build.ps1`（PyInstaller，产物在 `dist\wuxian\`）；发布包：`powershell scripts\pack.ps1`（Velopack）。

## 目录

```
src/wuxianworkshop/   App：core（帧格式、定位、解码、字体信箱）、transport（抓屏、链路协议）、agent（命令、检查、历史、探针）、
                      daemon（本机服务）、mcp、cli、installer（安装与自检）、ui（窗口）、
                      local（扩展：登记、检查、批准、扩展包、本机进程、反射）
addon/                游戏里的开发组件：!WuxianWorkshop、WoWBridge
sdk/python/           Python SDK（wuxiankit.py）：游戏外的程序听应用、调能力，写本机扩展
tests/                单元测试；tests/support 有合成屏幕和用 Lua 5.1 模拟的 WoW 客户端
scripts/              打包、发布、图标与数据包工具
notes/                各版本的更新说明
```

用 AI 参与开发？仓库根目录的 [AGENTS.md](AGENTS.md) 是写给编码 Agent 的说明，人也可以看。

## API 手册数据

`src/wuxianworkshop/data/api.json.gz` 是 [无限图鉴](https://wuxianwow.com/api/) 从游戏客户端整理的 API 手册（生成它的数据不在本
仓库）。其中的 API 文档和用法字符串取自游戏客户端，版权归暴雪娱乐所有。新的数据包发布在 wuxianwow.com/workshop/data，App 会自动
下载。

## 许可

- 代码：[MIT](LICENSE)。
- 第三方组件（htmx、Alpine.js、IBM Plex Mono 字体与 Python 依赖）见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- 「无限工坊」的名称与 logo 不随代码授权。
- 玩家自制、非官方，与暴雪娱乐、网易均无关联。《魔兽世界》（World of Warcraft）是暴雪娱乐的商标。

## 参与

欢迎提 Issue 和 Pull Request，见 [CONTRIBUTING.md](CONTRIBUTING.md)；安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

---

**English.** Wuxian Workshop lets your AI agent (Claude Code, Codex, Cursor, Trae, WorkBuddy or any MCP client) write and debug World of
Warcraft addons for the Chinese 1.60 "无限" client by itself: it checks the code before the game, hot-loads it into the
running game, tries it and reads back the errors (with stacks), prints and screenshots. The program and the addon talk
through a frame code drawn on the screen and a font mailbox — no memory reading, no injection, no synthetic input.
From 0.9.13 the agent can also turn an idea into an extension: a folder with a manifest that runs in the game, on this
PC (a process, with the Python SDK in `sdk/python/`) or both, shared as a pack the player approves before it runs.
Windows only, MIT licensed. Website: https://wuxianwow.com/workshop
