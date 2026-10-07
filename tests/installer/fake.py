"""A fake client folder in the v0.7 state (what the installer has to clean) and a fake addon/ source folder, both in a
temporary folder; the installer and the doctor tests share them."""
from pathlib import Path

from wuxianworkshop import __version__
from wuxianworkshop.installer import doctor

BUILD_INFO_HEADER = ("Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|CDN Key!HEX:16|Install Key!HEX:16|IM Size!DEC:4|CDN Path!STRING:0|"
                     "CDN Hosts!STRING:0|CDN Servers!STRING:0|Tags!STRING:0|Armadillo!STRING:0|Last Activated!STRING:0|Version!STRING:0|"
                     "KeyRing!HEX:16|Product!STRING:0")
CLIENT_VERSION = "1.60.1.70235"
USED_SLOTS = {1: b"used-1", 2: b"used-2", 9: b"used-9"}          # non-empty mailbox slots the install must not touch
FOREIGN_SLOT = 7                                                   # WoWBridge_S007 holds a file we did not write: left alone
EXPERIMENT_FILES = {"ack": ["001.wav", "256.wav"], "fresh": ["001.wav"], "never": ["001.wav"], "flag": ["001.wav"],
                    "poll": ["001.wav"], "ctl": ["empty.wav", "valid.wav"], "play": ["rt1.wav"], "font": ["levels.ttf"]}


def build_info_row(version=CLIENT_VERSION, product="wow_cn_beta", branch="cn", active="1"):
    return f"{branch}|{active}|570b334fc4e19e6309d416e029b612f4|07e1db8d231a023038e4a0d176fa4d56|||tpr/wow|host|http://host/|tags|||{version}||{product}"


def make_client(root, version=CLIENT_VERSION, mail_slots=10, build_info_rows=None, build_info_in_client=False):
    """<root>/World of Warcraft/_cn_beta_ as v0.7 left it; returns the client folder"""
    game = Path(root) / "World of Warcraft" / "_cn_beta_"
    addons = game / "Interface" / "AddOns"
    addons.mkdir(parents=True)
    (game / ".flavor.info").write_text("Product Flavor!STRING:0\nwow_cn_beta\n", encoding="utf-8")
    rows = build_info_rows if build_info_rows is not None else [build_info_row(version)]
    where = game if build_info_in_client else game.parent
    (where / ".build.info").write_text("\n".join([BUILD_INFO_HEADER, *rows]) + "\n", encoding="utf-8")
    (game / "WTF" / "Account" / "123#1" / "SavedVariables").mkdir(parents=True)
    # the old early addon and the old WoWBridge (v0.7 files, experiment folders, a partly used mailbox)
    (addons / "!WoWBridge").mkdir()
    (addons / "!WoWBridge" / "!WoWBridge.toc").write_text("## Interface: 16001\n## Title: WoWBridge (early)\n## Version: 0.7.0\n\nEarly.lua\n", encoding="utf-8")
    (addons / "!WoWBridge" / "Early.lua").write_text("-- early\n", encoding="utf-8")
    wb = addons / "WoWBridge"
    wb.mkdir()
    (wb / "WoWBridge.toc").write_text("## Interface: 16001\n## Title: WoWBridge\n## Version: 0.7.0\n## SavedVariables: WoWBridgeDB\n\n"
                                      "Config.lua\nCodec.lua\nWoWBridge.lua\n", encoding="utf-8")
    for f in ("Config.lua", "Codec.lua", "WoWBridge.lua"):
        (wb / f).write_text(f"-- old {f}\n", encoding="utf-8")
    for d, files in EXPERIMENT_FILES.items():
        (wb / d).mkdir()
        for f in files:
            (wb / d / f).write_bytes(b"" if f.endswith(".wav") else bytes(64))
    mail = wb / "mail"
    mail.mkdir()
    for i in range(1, mail_slots + 1):
        (mail / f"{i:05d}.ttf").write_bytes(USED_SLOTS.get(i, b""))
    (mail / "proc.ttf").write_bytes(b"proc-stamp")
    for i in range(1, 33):
        name = f"WoWBridge_S{i:03d}"
        (addons / name).mkdir()
        (addons / name / f"{name}.toc").write_text(f"## Interface: 16001\n## Title: WoWBridge slot {i:03d}\n## LoadOnDemand: 1\n\nInbox.lua\n", encoding="utf-8")
        (addons / name / "Inbox.lua").write_text('WoWBridge_SlotData = { stamp = "install" }\n', encoding="utf-8")
    (addons / f"WoWBridge_S{FOREIGN_SLOT:03d}" / "Notes.txt").write_text("someone's notes\n", encoding="utf-8")
    # an addon that is not ours
    (addons / "SomeOtherAddon").mkdir()
    (addons / "SomeOtherAddon" / "SomeOtherAddon.toc").write_text("## Interface: 16001\n## Title: Other\n\nOther.lua\n", encoding="utf-8")
    (addons / "SomeOtherAddon" / "Other.lua").write_text("-- other\n", encoding="utf-8")
    return game


def make_source(root, version=None, interface=16001):
    """a fake addon/ folder with the two product addons; returns it"""
    version = version or doctor.base_version(__version__)
    src = Path(root) / "addon"
    ww = src / "!WuxianWorkshop"
    ww.mkdir(parents=True)
    (ww / "!WuxianWorkshop.toc").write_text(f"## Interface: {interface}\n## Title: 无限工坊\n## Version: {version}\n"
                                            "## SavedVariables: WuxianWorkshopDB\n\nCore.lua\n", encoding="utf-8")
    (ww / "Core.lua").write_text("-- WuxianWorkshop core\n", encoding="utf-8")
    (ww / "README.md").write_text("not an addon file\n", encoding="utf-8")
    wb = src / "WoWBridge"
    wb.mkdir()
    (wb / "WoWBridge.toc").write_text(f"## Interface: {interface}\n## Title: WoWBridge\n## Version: {version}\n## OptionalDeps: !WuxianWorkshop\n"
                                      "## SavedVariables: WoWBridgeDB\n\nConfig.lua\nCodec.lua\nLink.lua\nUI.xml\nWoWBridge.lua\n", encoding="utf-8")
    for f in ("Config.lua", "Codec.lua", "Link.lua", "WoWBridge.lua"):
        (wb / f).write_text(f"-- new {f}\n", encoding="utf-8")
    (wb / "UI.xml").write_text("<Ui></Ui>\n", encoding="utf-8")
    (wb / "Media").mkdir()
    (wb / "Media" / "logo-small.tga").write_bytes(bytes(18) + bytes([255]) * 16)     # a texture: copied too
    return src
