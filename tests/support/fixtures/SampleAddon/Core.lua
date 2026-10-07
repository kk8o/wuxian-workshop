local name, ns = ...
ns.loads = (ns.loads or 0) + 1
print(("%s Core.lua run %d"):format(name, ns.loads))
return name, ns.loads
-- edited at 16:15:20
