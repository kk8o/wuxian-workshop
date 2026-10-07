"""无限工坊 (WuxianWorkshop): a two-way link between a World of Warcraft addon and a program on the same PC, with no
memory reading, injected code or synthetic input.

core        frame format v1, finding and decoding frames in an image, font packets and the font mailbox, the game folder
transport   reading the game window (GDI / Windows Graphics Capture) and the link protocol on top of the frames
agent       what an agent does over the link: commands and hot loading, snaps, debug.log, the frame monitor
daemon      the companion process that keeps the link running
cli         the `wuxian` command line; installer puts the addon into the game; mcp and ui are placeholders for now
paths       where the run-time files go (%LOCALAPPDATA%\\WuxianWorkshop, or WUXIAN_HOME)
"""
__version__ = "0.9.4"
