"""Point review independently reads both normalized originals in their native grids."""

from .channels import (
    adoption_matches,
    caliber_channels,
    caliber_key,
    point_channel_reading,
    resolve_adopted_caliber,
)
from .geometry import read_box
from .lighting import reliability, signed_response
from .photo import luminance


def _raw(image, x, y):
    rgb = image[y, x]
    gray = luminance(rgb)
    return {"x": x, "y": y, "rgb": rgb.tolist(), "gray": round(float(gray), 2),
            "valid": bool(reliability(rgb, gray))}, gray


def _classify(value, threshold):
    if value is None:
        return None
    if value >= threshold:
        return "positive"
    if value <= -threshold:
        return "negative"
    return "level"


def channel_review(left, right, settings, channel_calibration, x, y,
                   adoption=None, query=None, inside=True, gray_gain=1.0,
                   anchor_x=None):
    """Review one exact adopted caliber; never fall back to another channel."""
    key, source, caliber_column = resolve_adopted_caliber(
        adoption if adoption_matches(adoption, settings) else None,
        x, query, anchor_x=anchor_x,
    )
    review = {
        "caliber": key,
        "channels": caliber_channels(key) if key != "gray" and key is not None else None,
        "source": source,
        "caliber_column": caliber_column,
        "response": None,
        "status": "no_adoption" if key is None else "pending",
        "inside": bool(inside),
        "rgb_preserved": True,
        "right_x": x - settings["dx"],
        "right_y": y - settings["dy"],
    }
    if source == "no_adoption":
        review["status"] = "no_adoption"
        return review
    if source == "no_adopted_column":
        review["status"] = "no_adopted_column"
        return review
    if not inside:
        review["status"] = "outside_zone"
        return review

    rx, ry = review["right_x"], review["right_y"]
    if not (0 <= rx < right.shape[1] and 0 <= ry < right.shape[0]):
        review["status"] = "unpaired"
        return review

    if key == "gray":
        source_data, a = _raw(left, x, y)
        target_data, b = _raw(right, rx, ry)
        review["left"] = source_data
        review["right"] = target_data
        if not source_data["valid"] or not target_data["valid"]:
            review["status"] = "exposure"
            return review
        value = float(signed_response(a, b, gray_gain))
        review["response"] = round(value, 3)
        review["status"] = _classify(value, settings["threshold"])
        return review

    result = point_channel_reading(left[y, x], right[ry, rx], key, channel_calibration)
    review.update(result)
    if not result["valid"]:
        calibration_missing = any(
            not item["calibration_valid"] for item in result["checks"].values()
        )
        review["status"] = "channel_support" if calibration_missing else "channel_exposure"
        return review
    value = result["response"]
    review["status"] = _classify(value, settings["threshold"])
    return review


def inspect(left, right, settings, calibration, x, y,
            channel_calibration=None, adoption=None, forced_caliber=None,
            anchor_x=None):
    zone = read_box(settings["zone"], (left.shape[1], left.shape[0]), "量测框")
    source, a = _raw(left, x, y)
    rx, ry = x - settings["dx"], y - settings["dy"]
    alternative = None
    b = None
    if 0 <= rx < right.shape[1] and 0 <= ry < right.shape[0]:
        alternative, b = _raw(right, rx, ry)
    inside = zone.contains(x, y)
    report = {"left": source, "right": alternative, "inside": inside, "response": None,
              "status": "unpaired", "caliber": "gray"}
    if not inside:
        report["status"] = "outside_zone"
    elif alternative and (not source["valid"] or not alternative["valid"]):
        report["status"] = "exposure"
    elif alternative:
        value = float(signed_response(a, b, calibration["gain"]))
        report["response"] = round(value, 3)
        report["status"] = _classify(value, settings["threshold"]) or "level"

    review = channel_review(
        left, right, settings, channel_calibration or {}, x, y,
        adoption=adoption, query=forced_caliber, inside=inside,
        gray_gain=calibration["gain"], anchor_x=anchor_x,
    )
    report["gray_response"] = report["response"]
    report["gray_status"] = report["status"]
    report["gray_caliber"] = "gray"
    report["channel"] = review
    report["channel_review"] = review
    report["channel_response"] = review.get("response")
    report["supplement_response"] = review.get("response")
    report["reread_response"] = review.get("response")
    report["active_caliber"] = review.get("caliber")
    report["adopted"] = bool(adoption_matches(adoption, settings))

    # Once the user has adopted a route (or explicitly asks for a caliber),
    # the primary probe result is that exact caliber.  A missing channel stays
    # null here; the gray number remains available separately but is not used
    # as a hidden substitute.
    primary_channel = bool(forced_caliber) or report["adopted"]
    if primary_channel:
        report["response"] = review.get("response")
        report["status"] = review.get("status")
        report["caliber"] = review.get("caliber")
    return report
