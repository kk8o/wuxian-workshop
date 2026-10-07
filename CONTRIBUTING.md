# 参与无限工坊

谢谢你愿意帮忙！

## 报问题

- 用 Issue。请写清：无限工坊的版本（设置 → 关于与更新）、游戏客户端的版本、你做了什么、看到了什么。App「自检与修复」的结果和
  「开发台」的日志很有帮助——贴之前看一眼，别带上你自己的路径、账号或角色名。
- 安全问题不要发公开 Issue，见 [SECURITY.md](SECURITY.md)。

## 开发环境

- Windows 10 / 11（64 位）、Python 3.14、WebView2 运行时；要在游戏里试，还需要《魔兽世界：无限》客户端。
- 准备：

  ```powershell
  py -3.14 -m venv .venv
  .venv\Scripts\python.exe -m pip install -e ".[ui,mcp,test,build]"
  ```

  国内网络可以加 `-i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple`。
- 全部测试：`.venv\Scripts\python.exe -m unittest discover -s tests -t .`（几分钟：插件的 Lua 用 lupa 的 Lua 5.1 在模拟客户端里跑，
  守护进程的测试会起真实的本机服务）。
- 把开发组件装进游戏：`.venv\Scripts\wuxian install`，装完要完整重启游戏（游戏只在启动时发现新文件）。

## 约定

详细的目录和约定见 [AGENTS.md](AGENTS.md)（写给编码 Agent，也适合人看）。要点：

- 代码注释用英文；界面文案用中文。插件里给玩家看的文字放在 `Locale.lua`，中英两份都写。
- 协议两边互为镜像：帧格式（`core/frame.py` 与 `Codec.lua`、`Frame.lua`）、字体信箱（`core/mailbox.py` 与 `Mailbox.lua`）、链路
  （`transport/link.py` 与 `Link.lua`）。改一边必须同时改另一边，并补测试。
- 不模拟输入、不读游戏内存、不注入代码；只允许抓屏和读写插件目录里的文件。
- 新功能先补测试：Lua 的改动用 `tests/addon` 的模拟客户端覆盖，接口的改动用 `tests/daemon` 的真实服务覆盖。
- 程序和两个插件用同一个版本号，用 `scripts\set_version.py` 改。

## Pull Request

- 一个 PR 做一件事；说明为什么改、怎么验证的（测试结果，必要时附游戏里实测的截图）。
- 提交 PR 即表示你同意以 MIT 许可证发布这些改动。
