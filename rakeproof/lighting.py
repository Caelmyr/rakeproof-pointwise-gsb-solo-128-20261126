"""Compute the flat-patch exposure ratio and comparable, signed image samples."""

from dataclasses import dataclass

import numpy as np

from .geometry import common_bounds, read_box, to_right
from .photo import luminance


DARK_LIMIT = 15.0
CLIP_LIMIT = 249
MIN_FLAT_SUPPORT = 24
GAIN_BOUNDS = (0.4, 2.5)


def reliability(rgb, lum):
    return (lum > DARK_LIMIT) & (rgb.max(axis=-1) < CLIP_LIMIT)


def flat_gain(left, right, settings, common):
    if not settings["balance"]:
        return {"gain": 1.0, "support": 0, "state": "off"}
    patch = common.intersect(read_box(settings["flat"], (left.shape[1], left.shape[0]), "平面参照框"))
    if not patch.area:
        return {"gain": 1.0, "support": 0, "state": "insufficient"}
    lp = left[patch.slices]
    rp = right[to_right(patch, settings).slices]
    a, b = luminance(lp), luminance(rp)
    valid = reliability(lp, a) & reliability(rp, b)
    count = int(np.count_nonzero(valid))
    if count < MIN_FLAT_SUPPORT:
        return {"gain": 1.0, "support": count, "state": "insufficient"}
    ratio = float(np.median(a[valid] / b[valid]))
    if not GAIN_BOUNDS[0] <= ratio <= GAIN_BOUNDS[1]:
        return {"gain": 1.0, "support": count, "state": "outside_range"}
    return {"gain": ratio, "support": count, "state": "applied"}


def signed_response(left, right, gain):
    corrected = right * gain
    return 100 * (left - corrected) / np.maximum(left + corrected, 1)


@dataclass(frozen=True)
class Evidence:
    # Arrays are local to overlap∩zone, but consumers report original left coordinates.
    bounds: object
    common: object
    calibration: dict
    left_gray: np.ndarray
    right_gray: np.ndarray
    valid: np.ndarray
    response: np.ndarray


def sample_zone(left, right, settings):
    left_size = left.shape[1], left.shape[0]
    right_size = right.shape[1], right.shape[0]
    common = common_bounds(left_size, right_size, settings)
    bounds = common.intersect(read_box(settings["zone"], left_size, "量测框"))
    calibration = flat_gain(left, right, settings, common)
    if not bounds.area:
        shape = bounds.height, bounds.width
        empty = np.empty(shape, dtype=np.float32)
        return Evidence(bounds, common, calibration, empty, empty, np.zeros(shape, dtype=bool), empty)
    lp = left[bounds.slices]
    rp = right[to_right(bounds, settings).slices]
    a, b = luminance(lp), luminance(rp)
    valid = reliability(lp, a) & reliability(rp, b)
    response = signed_response(a, b, calibration["gain"])
    return Evidence(bounds, common, calibration, a, b, valid, response)
