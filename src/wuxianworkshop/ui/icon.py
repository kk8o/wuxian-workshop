"""The app's icon. The logo is static/logo.svg (the code brackets round the woven ∞ of wuxianwow.com, on its dark tile
with the gold frame and studs) and static/logo-small.svg (the same mark simplified for 24 px and less);
scripts/make_icons.py renders them into static/wuxian.ico (16 to 256 px: the exe, the window, the installer and the
tray, which takes its own small size from it) and static/icon.png (256 px). Re-render after changing either SVG.
"""
from . import STATIC_DIR

ICO_PATH = STATIC_DIR / "wuxian.ico"
PNG_PATH = STATIC_DIR / "icon.png"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
TILE = "#1b1c21"                          # the logo's background


def load():
    """the 256 px PNG as an RGBA image (what pystray takes); a plain tile when the file is missing"""
    from PIL import Image
    try:
        with Image.open(PNG_PATH) as img:
            return img.convert("RGBA")
    except OSError:
        return Image.new("RGBA", (32, 32), TILE)


def ico_sizes(path=ICO_PATH):
    """the sizes an .ico holds, smallest first"""
    import struct
    data = path.read_bytes()
    count = struct.unpack("<HHH", data[:6])[2]
    return sorted((data[6 + 16 * i] or 256) for i in range(count))
