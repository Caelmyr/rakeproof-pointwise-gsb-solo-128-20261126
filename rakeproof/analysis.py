"""Summarize the adopted measurement and render a full-coordinate sign map."""

import numpy as np
from PIL import Image

from .geometry import read_box
from .section import extract


def summarize(card, evidence):
    settings = card["settings"]
    zone = read_box(settings["zone"], card["left_size"], "量测框")
    trustworthy = evidence.response[evidence.valid]
    threshold = settings["threshold"]
    return {
        "zone": zone.record(), "common": evidence.common.record(), "bounds": evidence.bounds.record(),
        "calibration": evidence.calibration,
        "counts": {
            "zone": zone.area, "paired": evidence.bounds.area, "valid": len(trustworthy),
            "positive": int(np.count_nonzero(trustworthy >= threshold)),
            "negative": int(np.count_nonzero(trustworthy <= -threshold)),
            "median": round(float(np.median(trustworthy)), 3) if trustworthy.size else None,
        },
        "section": extract(evidence, settings),
    }


def map_image(size, evidence, threshold):
    img = Image.new("RGBA", tuple(size), (0, 0, 0, 0))
    bounds = evidence.bounds
    if not bounds.area:
        return img
    response = evidence.response
    valid = evidence.valid
    ys = np.arange(bounds.top, bounds.bottom, dtype=np.int32)[:, None]
    xs = np.arange(bounds.left, bounds.right, dtype=np.int32)[None, :]
    data = np.zeros((*response.shape, 4), dtype=np.uint8)
    positive = valid & (response >= threshold)
    negative = valid & (response <= -threshold)
    intensity = np.clip(np.rint(125 + 1.3 * np.abs(response)), 0, 238).astype(np.uint8)
    data[positive, :3] = (218, 99, 42)
    data[negative, :3] = (36, 118, 186)
    data[positive | negative, 3] = intensity[positive | negative]
    stripe = ~valid & (((xs + ys) % 8) < 3)
    data[stripe] = (74, 81, 92, 140)
    img.paste(Image.fromarray(data), (bounds.left, bounds.top))
    return img
