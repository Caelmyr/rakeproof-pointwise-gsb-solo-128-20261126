"""Point review independently reads both normalized originals in their native grids."""

from .geometry import read_box
from .lighting import reliability, signed_response
from .photo import luminance


def _raw(image, x, y):
    rgb = image[y, x]
    gray = luminance(rgb)
    return {"x": x, "y": y, "rgb": rgb.tolist(), "gray": round(float(gray), 2),
            "valid": bool(reliability(rgb, gray))}, gray


def inspect(left, right, settings, calibration, x, y):
    zone = read_box(settings["zone"], (left.shape[1], left.shape[0]), "量测框")
    source, a = _raw(left, x, y)
    rx, ry = x - settings["dx"], y - settings["dy"]
    alternative = None
    b = None
    if 0 <= rx < right.shape[1] and 0 <= ry < right.shape[0]:
        alternative, b = _raw(right, rx, ry)
    inside = zone.contains(x, y)
    report = {"left": source, "right": alternative, "inside": inside, "response": None, "status": "unpaired"}
    if not inside:
        report["status"] = "outside_zone"
    elif alternative and (not source["valid"] or not alternative["valid"]):
        report["status"] = "exposure"
    elif alternative:
        value = float(signed_response(a, b, calibration["gain"]))
        report["response"] = round(value, 3)
        report["status"] = "positive" if value >= settings["threshold"] else "negative" if value <= -settings["threshold"] else "level"
    return report
