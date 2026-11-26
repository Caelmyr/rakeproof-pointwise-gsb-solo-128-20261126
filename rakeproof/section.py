"""Read a chosen horizontal section and find opposite-light response runs."""

import numpy as np


def extract(evidence, settings):
    y = settings["scan_y"]
    bounds = evidence.bounds
    if not bounds.area or not bounds.top <= y < bounds.bottom:
        return {"y": y, "origin": None, "samples": [], "runs": []}
    row = y - bounds.top
    valid = evidence.valid[row]
    a = evidence.left_gray[row]
    b = evidence.right_gray[row]
    result = evidence.response[row]
    threshold = settings["threshold"]
    samples = []
    for offset, response in enumerate(result):
        x = offset + bounds.left
        samples.append({
            "x": x, "right_x": x - settings["dx"], "right_y": y - settings["dy"],
            "left_gray": round(float(a[offset]), 2), "right_gray": round(float(b[offset]), 2),
            "value": round(float(response), 3) if valid[offset] else None,
        })
    direction = np.select((result >= threshold, result <= -threshold), (1, -1), default=0)
    direction[~valid] = 0
    boundaries = np.flatnonzero(np.diff(np.concatenate(([0], direction, [0]))))
    runs = []
    for low, high in zip(boundaries[:-1], boundaries[1:]):
        sign = int(direction[low])
        if not sign:
            continue
        values = result[low:high]
        peak = int(low) + int(np.argmax(np.abs(values)))
        runs.append({
            "start": int(low) + bounds.left, "end": int(high) + bounds.left, "direction": sign,
            "peak_x": peak + bounds.left, "peak_value": round(float(result[peak]), 3),
        })
    return {"y": y, "origin": bounds.left, "samples": samples, "runs": runs}
