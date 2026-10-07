-- a file the agent hot-loads: runs with the name and namespace of no addon
local t0 = debugprofilestop()
local n = 0
for i = 1, 100000 do n = n + i end
print("hello.lua ran", n)
return GetBuildInfo(), ("%.2f ms"):format(debugprofilestop() - t0)
