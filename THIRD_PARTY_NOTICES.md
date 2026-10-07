# 第三方组件与声明 · Third-party notices

无限工坊自己的代码以 MIT 许可证发布（见 [LICENSE](LICENSE)）。下面这些不是我们写的，各自按原许可证使用。

## 随仓库一起分发的

| 组件 | 位置 | 许可证 |
| --- | --- | --- |
| [htmx](https://htmx.org/) 2.0.11 | `src/wuxianworkshop/ui/static/htmx.min.js` | Zero-Clause BSD（0BSD） |
| [Alpine.js](https://alpinejs.dev/) 3.17.4 | `src/wuxianworkshop/ui/static/alpine.min.js` | MIT，Copyright © 2019-2021 Caleb Porzio and contributors |
| [IBM Plex Mono](https://github.com/IBM/plex)（拉丁字符子集，400、500 两个字重） | `src/wuxianworkshop/ui/static/fonts/` | SIL Open Font License 1.1，全文见同目录的 `IBM-Plex-Mono-OFL.txt` |

## 安装或打包时引入的 Python 依赖

`pip install` 时下载，发布包（PyInstaller onedir）里带着它们：

| 包 | 许可证 |
| --- | --- |
| numpy | BSD-3-Clause |
| Pillow | MIT-CMU（HPND） |
| lupa（内含 Lua 5.1） | MIT（Lua：MIT） |
| pywebview | BSD-3-Clause |
| pythonnet | MIT |
| pystray | LGPL-3.0（发布包里它是单独的文件，可以替换） |
| mcp | MIT |
| starlette、uvicorn、websockets | BSD-3-Clause |
| velopack | MIT |
| PyInstaller（只用于打包） | GPL-2.0 附打包例外，打出的程序不受 GPL 约束 |

测试另用 fontTools（MIT）和 httpx（BSD-3-Clause）。

## 游戏数据

`src/wuxianworkshop/data/api.json.gz`（API 手册数据包）由 [无限图鉴](https://wuxianwow.com/api/) 从《魔兽世界：无限》客户端整理：
其中的 API 文档、用法字符串取自游戏客户端，版权归暴雪娱乐所有；手册文字由无限图鉴编写。

## 商标

无限工坊是玩家自制的非官方项目，与暴雪娱乐、网易均无关联。《魔兽世界》（World of Warcraft）是暴雪娱乐的商标。
「无限工坊」的名称与 logo 不随代码授权。
