# 安全 · Security

## 报告漏洞

请不要发公开 Issue。发邮件到 contact@wuxianwow.com，写清无限工坊的版本、复现步骤和影响，我们会尽快回复。

Please do not open a public issue; write to contact@wuxianwow.com with the version, the steps and the impact.

## 设计上的安全边界

- 本机服务只监听 127.0.0.1 的随机端口；每次启动生成新的 token（存在 `%LOCALAPPDATA%\WuxianWorkshop\state\daemon.json`），
  每个请求都要带它，并且校验 Host 与 Origin，网页不能借浏览器调用它。
- 游戏里的开发组件只执行本机服务送来的代码：服务把代码写进插件目录里的字体文件，插件读回来执行。能写你游戏插件目录的
  程序本来就能改任何插件，这一点和普通插件相同。
- 不读游戏内存、不注入代码、不模拟按键；重载界面只能由玩家在游戏里点按钮。
- 程序更新经 HTTPS 从 wuxianwow.com/workshop/releases 下载；安装包暂时没有代码签名。
