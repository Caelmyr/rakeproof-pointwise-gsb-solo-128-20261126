"""Read a chosen horizontal section and find opposite-light response runs."""

import numpy as np

from . import profile


def extract(evidence, settings):
    y = settings["scan_y"]
    bounds = evidence.bounds
    threshold = settings["threshold"]
    result = {"y": y, "origin": None, "samples": [], "runs": [],
              "profile": profile.unavailable("outside_zone")}
    if not bounds.area or not bounds.top <= y < bounds.bottom:
        return result
    row = y - bounds.top
    valid = evidence.valid[row]
    a = evidence.left_gray[row]
    b = evidence.right_gray[row]
    response = evidence.response[row]
    result["origin"] = bounds.left
    samples = []
    for offset, value in enumerate(response):
        x = offset + bounds.left
        samples.append({
            "x": x, "right_x": x - settings["dx"], "right_y": y - settings["dy"],
            "left_gray": round(float(a[offset]), 2), "right_gray": round(float(b[offset]), 2),
            "value": round(float(value), 3) if valid[offset] else None,
        })
    result["samples"] = samples
    direction = np.select((response >= threshold, response <= -threshold), (1, -1), default=0)
    direction[~valid] = 0
    boundaries = np.flatnonzero(np.diff(np.concatenate(([0], direction, [0]))))
    runs = []
    for low, high in zip(boundaries[:-1], boundaries[1:]):
        sign = int(direction[low])
        if not sign:
            continue
        values = response[low:high]
        peak = int(low) + int(np.argmax(np.abs(values)))
        runs.append({
            "start": int(low) + bounds.left, "end": int(high) + bounds.left, "direction": sign,
            "peak_x": peak + bounds.left, "peak_value": round(float(response[peak]), 3),
        })
    result["runs"] = runs
    gaps, counted = profile.gap_runs(valid, bounds.left)
    ends_are_zone = bounds.left == settings["zone"]["left"] and bounds.right == settings["zone"]["right"]
    if not ends_are_zone:
        result["profile"] = profile.unavailable("ends_unpaired", gaps, counted)
    elif counted:
        result["profile"] = profile.unavailable("row_gap", gaps, counted)
    else:
        result["profile"] = profile.close(response, threshold, bounds.left)
    return result

