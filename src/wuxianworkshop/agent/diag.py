"""The probe frame's payload: the addon's "key=value;..." diagnostics (physical size, UI scale, gamma, parent frame)."""


def parse_diag(payload):
    """b"pw=1920;ph=1080;es=0.5" -> {"pw": "1920", "ph": "1080", "es": "0.5"}"""
    out = {}
    for part in payload.decode("utf-8", "replace").split(";"):
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out
