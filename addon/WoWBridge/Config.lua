-- WoWBridge defaults. forwardDebug, hotLoad and autoLink are switched in game with /wb set <key> on|off, parent and snap
-- with /wb parent and /wb snap; what is set that way is kept per character in WoWBridgeDB.settings and overrides this file.
WoWBridge_Config = {
	autoLink = true,       -- connect to the companion after entering the world (HELLO frame, then heartbeats)
	forwardDebug = true,   -- send Lua errors (every addon's), print() output, Lua warnings and blocked actions to the
	                       -- companion, which writes them to debug.log (%LOCALAPPDATA%\WuxianWorkshop\logs)
	hotLoad = true,        -- run Lua the companion sends (an agent's run / load); each run shows one line in the chat
	cell = 4,              -- cell size in physical pixels
	mode = 1,              -- data frames: 0 = black/white, 1 = 8 colours, 2 = 4 levels per channel (/wb mode)
	parent = "UIParent",   -- "UIParent" or "WorldFrame" (the latter may stay visible when Alt+Z hides the UI)
	snap = "default",      -- texture pixel snapping: "default", "on" or "off"
}
