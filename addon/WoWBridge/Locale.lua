-- WoWBridge's words in two languages. The language follows the client (zhCN / zhTW: Chinese, else English) until
-- /wb lang or the settings panel picks one (WoWBridgeDB.settings.lang = "zhCN" | "enUS"; "auto" or nil follows the
-- client). ns.L.KEY is the text in the language in force: a key missing in Chinese falls back to English, a key missing in
-- both shows as itself. What goes to the companion (debug messages, RUN / RELOAD results, test messages) stays English:
-- programs read it. ns.OnLanguage(fn) runs fn after every change (the panel and the minimap button relabel themselves).
local _, ns = ...

local T = {}

T.enUS = {
	-- chat
	LOADED = "developer addon %s loaded - /wb opens the panel, /wb help lists the commands",
	FRAME_ERROR = "frame error: %s",
	USAGE_SEND = "usage: /wb send <text>",
	QUEUED = "message #%d queued (link %s)",
	BURST = "burst: %d messages queued; a BURST DONE message follows once all are acknowledged",
	LONG = "long message #%d queued: %d parts",
	STREAM = "stream: a message every 0.3 s for %d s (every 20th of 4 KB); STREAM DONE follows",
	SET = "%s = %s (kept for this character)",
	USAGE_SET = "usage: /wb set forwardDebug|hotLoad|autoLink on|off",
	FRAME_HIDDEN = "frame hidden",
	PARENT = "the frame hangs from %s",
	SNAP = "pixel snapping: %s",
	MODE = "data frames use mode %s",
	STATUS = "%s, session %d, %s",
	ON_SCREEN = "%s frame #%d on screen",
	NO_FRAME = "no frame on screen",
	HELP = "/wb opens the panel | link | send <text> | burst [n] | long [bytes] | stream [s] | set <key> on|off | "
		.. "lang auto|zh|en | unlock | lock | reset | on | off | parent ui|world | snap default|on|off | mode 0|1|2 | diag",
	LANG = "language: %s",
	USAGE_LANG = "usage: /wb lang auto|zh|en",
	LANG_AUTO = "follow the client",
	LANG_AUTO_SHORT = "Auto",
	LINK_STOPPED = "link stopped: the frame is hidden",
	LINK_STARTED = "link started: the frame shows again",
	COMPANION = "companion %s: frames %dx%d (long messages %dx%d), mode %d, %d in flight, %.2f s a frame, a heartbeat every %d s",
	COMMAND = "companion command: %s",
	CONNECTED = "connected to the companion (mailbox slot %d)",
	OFFLINE = "no packet from the companion for %d s: offline, %d messages waiting",
	CUT = "message cut from %d to %d bytes (255 parts)",
	LINK_STATUS = "link %s, mailbox slot %d (%s, %d left), %d packets read; %d waiting, %d acknowledged (avg %.2f s, max %.2f s), "
		.. "%d parts shown again; drawing %.1f ms avg, %.1f ms max",
	AGENT_CODE = "agent code %s (%d B): %s",
	AGENT_OK = "ok",
	AGENT_ERROR = "error",
	-- dialogs
	RELOAD_ASK = "The agent asks for a UI reload",
	RELOAD_NOW = "Reload now",
	RELOAD_LATER = "Later",
	RELOAD_CHAT = "the agent asks for a UI reload: click the button in the middle of the screen",
	SLOTS_GONE_CHAT = "mailbox slots used up: nothing from the companion gets through any more. Exit the game fully and start it "
		.. "again to get them all back (a /reload or going back to the character screen does not)",
	SLOTS_GONE_DIALOG = "Mailbox slots used up: nothing from the companion gets through any more.\nExit the game fully and start it "
		.. "again to get them all back.",
	GOT_IT = "Got it",
	SLOTS_LEFT = "%d mailbox slots left (about %.1f h when idle). When they are gone nothing from the companion gets through; only a "
		.. "full restart of the game brings them back (a /reload or the character screen does not)",
	-- frame position
	MOVER = "Drag to move the frame · the dashed box is the most it takes (long messages) · lock it in /wb",
	UNLOCKED = "frame unlocked: drag it so that the dashed box (the most it takes) covers nothing you need; /wb lock (or the "
		.. "settings) fixes it",
	LOCKED = "frame locked at %d, %d",
	RESET = "frame back in the top-left corner",
	-- settings panel (BRAND: also the chat prefix and the dialogs' title)
	BRAND = "Wuxian Workshop",
	SUBTITLE = "Developer addon · WoWBridge %s",
	PANEL_TITLE = "Wuxian Workshop · WoWBridge",
	TAB_OVERVIEW = "Overview",
	TAB_SETTINGS = "Settings",
	TAB_DIAG = "Diagnostics",
	OV_ONLINE = "Connected to the app",
	OV_HELLO = "Waiting for the app",
	OV_OFF = "Not connected",
	WHY_ONLINE = "The agent can run code in this game and read its errors and screenshots.",
	WHY_HELLO = "Start the Wuxian Workshop app on this PC. Still nothing: its 自检与修复 (self-check) page says why.",
	WHY_OFF = "The frame is hidden, so the app cannot connect: turn the switch below on.",
	SECTION_SESSION = "Activity",
	RUNS_NONE = "Agent code: nothing run since the last reload",
	RUNS = "Agent code ran %d times, %d failed",
	LAST_RUN = "Last: %s · %s · %s",
	RUN_SNIPPET = "a Lua snippet",
	AGO_S = "%d s ago",
	AGO_M = "%d min ago",
	AGO_H = "%d h ago",
	ERRORS = "Lua errors: %d · warnings: %d · blocked: %d",
	NOT_SENT = " (not sent to the app)",
	NOTICE_UNLOCKED = "The frame is unlocked: drag it where it covers nothing, then lock it.",
	NOTICE_HOTLOAD = "Hot loading is off: code the agent sends does not run.",
	BTN_TURN_ON = "Turn on",
	SECTION_POSITION = "Frame position",
	SECTION_OPTIONS = "Options",
	SECTION_LINK = "Link",
	SECTION_TESTS = "Link tests",
	DIAG_NOTE = "For finding out why the app does not connect; the app's 自检与修复 (self-check) page checks the rest.",
	STATE_ONLINE = "online",
	STATE_HELLO = "waiting for the app",
	STATE_OFF = "off",
	ST_LINK = "Link: %s",
	ST_SESSION = "session %d",
	ST_SLOTS = "Mailbox slots left: %d (about %.1f h when idle)",
	ST_QUEUE = "Waiting %d · acknowledged %d · heartbeat %d s",
	OPT_LINK = "Show the frame and connect to the app",
	OPT_LINK_TIP = "Off: the frame disappears and the link stops (the app shows offline).",
	BTN_UNLOCK = "Unlock position",
	BTN_LOCK = "Lock position",
	BTN_RESET = "Reset position",
	POSITION = "Position: %d, %d (pixels from the top-left)",
	OPT_DEBUG = "Send Lua errors and print() output to the app",
	OPT_HOTLOAD = "Allow hot loading (run Lua the agent sends)",
	BTN_SEND = "Test message",
	BTN_BURST = "10 at once",
	BTN_LONG = "Long (6 KB)",
	BTN_STATUS = "Link status in chat",
	BTN_RESTART = "Reconnect",
	TEST_TEXT = "test from the settings panel",
	OPT_MINIMAP = "Show the minimap button",
	LANGUAGE = "Language",
	FOOTER = "Logs, screenshots and the agent's work: the app's 开发台 (dev console) page.",
	-- minimap button and the options
	MM_LEFT = "Left-click: the panel",
	MM_RIGHT = "Right-click: link status",
	MM_DRAG = "Drag: move this button",
	OPTIONS_TEXT = "Wuxian Workshop: make WoW addons with AI. WoWBridge is its part in the game: it runs the Lua the AI agent "
		.. "sends (hot loading) and hands errors, prints and screenshots to the Wuxian Workshop app.",
	OPTIONS_OPEN = "Open the panel",
	WEBSITE = "Website",
	BTN_COPY = "Copy link",
	COPY_TIP = "Selected: press Ctrl+C to copy, Esc to close",
}

T.zhCN = {
	LOADED = "开发组件 %s 已加载：/wb 打开面板，/wb help 查看命令",
	FRAME_ERROR = "帧码出错：%s",
	USAGE_SEND = "用法：/wb send <文本>",
	QUEUED = "消息 #%d 已排队（链路：%s）",
	BURST = "连发：%d 条消息已排队，全部确认后会发出 BURST DONE",
	LONG = "长消息 #%d 已排队：%d 段",
	STREAM = "持续发送：%d 秒内每 0.3 秒一条（每 20 条有一条 4 KB），结束后发出 STREAM DONE",
	SET = "%s = %s（为这个角色保存）",
	USAGE_SET = "用法：/wb set forwardDebug|hotLoad|autoLink on|off",
	FRAME_HIDDEN = "帧码已隐藏",
	PARENT = "帧码挂在 %s 上",
	SNAP = "像素对齐：%s",
	MODE = "数据帧使用模式 %s",
	STATUS = "%s，会话 %d，%s",
	ON_SCREEN = "屏幕上是 %s 帧 #%d",
	NO_FRAME = "屏幕上没有帧码",
	HELP = "/wb 打开面板 | link 链路状态 | send <文本> | burst [n] | long [字节] | stream [秒] | set <开关> on|off | "
		.. "lang auto|zh|en 语言 | unlock 解锁位置 | lock 锁定 | reset 复位 | on 打开 | off 关闭 | parent ui|world | snap default|on|off | "
		.. "mode 0|1|2 | diag",
	LANG = "语言：%s",
	USAGE_LANG = "用法：/wb lang auto|zh|en",
	LANG_AUTO = "跟随客户端",
	LANG_AUTO_SHORT = "自动",
	LINK_STOPPED = "链路已停止，帧码已隐藏",
	LINK_STARTED = "链路已启动，帧码重新显示",
	COMPANION = "无限工坊 App %s：帧 %dx%d（长消息 %dx%d），模式 %d，同时 %d 条，每帧 %.2f 秒，心跳每 %d 秒",
	COMMAND = "App 发来的命令：%s",
	CONNECTED = "已连接无限工坊 App（信箱槽位 %d）",
	OFFLINE = "%d 秒没有收到 App 的包：离线，%d 条消息待发",
	CUT = "消息从 %d 字节截到 %d 字节（最多 255 段）",
	LINK_STATUS = "链路%s，信箱槽位 %d（%s，剩 %d），已读 %d 个包；待发 %d 条，已确认 %d 条（平均 %.2f 秒，最长 %.2f 秒），"
		.. "补发 %d 段；绘制平均 %.1f 毫秒，最长 %.1f 毫秒",
	AGENT_CODE = "Agent 代码 %s（%d 字节）：%s",
	AGENT_OK = "成功",
	AGENT_ERROR = "出错",
	RELOAD_ASK = "Agent 请求重载界面",
	RELOAD_NOW = "立即重载",
	RELOAD_LATER = "稍后",
	RELOAD_CHAT = "Agent 请求重载界面：请点屏幕中间的按钮",
	SLOTS_GONE_CHAT = "信箱槽位已用完：App 的消息进不来了。完整退出游戏再启动，槽位会全部恢复（/reload 和返回角色选择都不行）。",
	SLOTS_GONE_DIALOG = "信箱槽位已用完，App 的消息进不来了。\n完整退出游戏再启动，槽位会全部恢复。",
	GOT_IT = "知道了",
	SLOTS_LEFT = "信箱槽位还剩 %d 个（空闲时约 %.1f 小时）。用完后 App 的消息就进不来了，要完整退出游戏再启动才能恢复，"
		.. "/reload 和返回角色选择都不行。",
	MOVER = "拖动以移动帧码 · 虚线框是它最大时（长消息）占的范围 · 在设置里锁定",
	UNLOCKED = "帧码已解锁：拖动它，让虚线框（它最大时的范围）不挡住要用的东西；/wb lock 或设置面板里锁定",
	LOCKED = "帧码已锁定在 %d, %d",
	RESET = "帧码回到左上角",
	BRAND = "无限工坊",
	SUBTITLE = "开发组件 · WoWBridge %s",
	PANEL_TITLE = "无限工坊 · 开发组件",
	TAB_OVERVIEW = "概览",
	TAB_SETTINGS = "设置",
	TAB_DIAG = "诊断",
	OV_ONLINE = "已连接 App",
	OV_HELLO = "等待 App",
	OV_OFF = "未连接",
	WHY_ONLINE = "Agent 可以在这个游戏里运行代码，读报错和截图。",
	WHY_HELLO = "请打开这台电脑上的无限工坊 App；还连不上，看 App 的「自检与修复」。",
	WHY_OFF = "帧码已隐藏，App 连不上：打开下面的开关即可连接。",
	SECTION_SESSION = "近况",
	RUNS_NONE = "Agent 代码：重载界面后还没有运行过",
	RUNS = "Agent 代码：运行 %d 次，出错 %d 次",
	LAST_RUN = "最近：%s · %s · %s",
	RUN_SNIPPET = "一段 Lua",
	AGO_S = "%d 秒前",
	AGO_M = "%d 分钟前",
	AGO_H = "%d 小时前",
	ERRORS = "Lua 报错 %d 次 · 警告 %d 次 · 被拦截 %d 次",
	NOT_SENT = "（没有发给 App）",
	NOTICE_UNLOCKED = "帧码已解锁：拖到不挡东西的地方，然后锁定。",
	NOTICE_HOTLOAD = "热加载已关闭：Agent 发来的代码不会运行。",
	BTN_TURN_ON = "打开",
	SECTION_POSITION = "帧码位置",
	SECTION_OPTIONS = "选项",
	SECTION_LINK = "链路",
	SECTION_TESTS = "链路测试",
	DIAG_NOTE = "用来排查连不上的原因；其余的在 App 的「自检与修复」里检查。",
	STATE_ONLINE = "在线",
	STATE_HELLO = "等待 App",
	STATE_OFF = "已关闭",
	ST_LINK = "链路：%s",
	ST_SESSION = "会话 %d",
	ST_SLOTS = "信箱槽位剩余：%d（空闲时约 %.1f 小时）",
	ST_QUEUE = "待发：%d · 已确认：%d · 心跳每 %d 秒",
	OPT_LINK = "显示帧码并连接 App",
	OPT_LINK_TIP = "关闭后帧码消失、链路停止（App 显示离线）。",
	BTN_UNLOCK = "解锁位置",
	BTN_LOCK = "锁定位置",
	BTN_RESET = "复位",
	POSITION = "位置：%d, %d（物理像素，从左上角算）",
	OPT_DEBUG = "把 Lua 报错和 print 输出发给 App",
	OPT_HOTLOAD = "允许热加载（执行 Agent 发来的 Lua）",
	BTN_SEND = "测试消息",
	BTN_BURST = "连发 10 条",
	BTN_LONG = "长消息 6 KB",
	BTN_STATUS = "链路状态发到聊天框",
	BTN_RESTART = "重新连接",
	TEST_TEXT = "来自设置面板的测试消息",
	OPT_MINIMAP = "显示小地图按钮",
	LANGUAGE = "语言",
	FOOTER = "日志、截图和 Agent 的工作情况，在无限工坊 App 的「开发台」。",
	MM_LEFT = "左键：打开面板",
	MM_RIGHT = "右键：链路状态",
	MM_DRAG = "拖动：移动这个按钮",
	OPTIONS_TEXT = "无限工坊：用 AI 写魔兽插件。开发组件是它在游戏里的部分：执行 AI Agent 发来的 Lua（热加载），把报错、print 输出和截图交给无限工坊 App。",
	OPTIONS_OPEN = "打开面板",
	WEBSITE = "官网",
	BTN_COPY = "复制网址",
	COPY_TIP = "已选中：按 Ctrl+C 复制，Esc 关闭",
}

ns.LANGUAGES = { "auto", "zhCN", "enUS" }
ns.lang = "enUS"
local listeners = {}

local L = setmetatable({}, { __index = function(_, key)
	local own = T[ns.lang]
	return (own and own[key]) or T.enUS[key] or key
end })
ns.L = L

-- the language a setting stands for: "zhCN" / "enUS" as set, else the client's
function ns.ResolveLanguage(setting)
	if setting == "zhCN" or setting == "enUS" then return setting end
	local loc = GetLocale and GetLocale() or "enUS"
	return (loc == "zhCN" or loc == "zhTW") and "zhCN" or "enUS"
end

-- the name of a language setting, in the language in force (short: for a button)
function ns.LanguageName(setting, short)
	if setting == "zhCN" then return "中文" elseif setting == "enUS" then return "English" end
	return short and L.LANG_AUTO_SHORT or L.LANG_AUTO
end

-- the link's state ("online", "hello", "off") in words
function ns.StateName(state)
	if state == "online" then return L.STATE_ONLINE elseif state == "off" then return L.STATE_OFF end
	return L.STATE_HELLO
end

function ns.SetLanguage(setting)
	ns.lang = ns.ResolveLanguage(setting)
	for _, fn in ipairs(listeners) do pcall(fn) end
end

function ns.OnLanguage(fn)
	listeners[#listeners + 1] = fn
end

ns.lang = ns.ResolveLanguage(nil)
