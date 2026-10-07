"""debug.log (paths.logs_dir): the addon's debug output and the companion's notes for the agent, one entry per message."""
import time


def debug_writer(path):
    """on_debug for the companion: one entry per message, continuation lines indented"""
    def write(kind, text):
        lines = text.split("\n")
        entry = f"{time.strftime('%H:%M:%S')} {kind} {lines[0]}\n" + "".join(f"    {line}\n" for line in lines[1:])
        with path.open("a", encoding="utf-8") as fh:
            fh.write(entry)
    return write
