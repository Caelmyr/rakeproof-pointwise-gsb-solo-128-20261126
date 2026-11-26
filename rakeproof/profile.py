"""Turn a scanline's signed contrast into a relative height that closes at both ends.

The signed response is a slope proxy: positive means the left photo is relatively
bright at that column.  Integrating it gives a height profile, but a constant
offset in the response - what remains of an exposure scale difference after the
flat balance, or any one-sided bias - integrates into a straight ramp and leaves
the two ends at different heights even when both sit on the same table.

The measured row is only closed when both of its ends sit on the two zone
positions the operator confirmed to be the same table height.  Removing the
end-to-end drift is then unavoidable, and where it is removed is the real
choice described at the call site below.
"""

import numpy as np

# Equal step per column: the drift is a constant response offset, whose integral
# is a straight line, so a straight correction matches the physical error and
# adds no curvature anywhere.  It is also the minimax allocation - of every
# distribution that removes the same total, it changes the largest single column
# the least.  Flat-weighted spreading bows flat ground into a dip, steep-weighted
# spreading carves relief out of the panel; both were measured against known
# relief in the synthetic exercises and neither beat this line on shape.
METHOD = "equal_step"
VERSION = 1
KEEP = 0.5
TOLERANCE = 1e-9
REDISTRIBUTE_ROUNDS = 64


def unavailable(reason, gaps=(), counted=0):
    return {
        "method": METHOD, "version": VERSION, "status": "open", "reason": reason,
        "gaps": [list(gap) for gap in gaps], "gap_columns": counted,
        "drift": None, "step": None, "step_low": None, "step_high": None,
        "guarded": 0, "guarded_runs": [],
        "high": None, "high_x": None, "high_raw": None,
        "low": None, "low_x": None, "low_raw": None,
        "origin": None, "heights": None, "raw": None,
    }


def gap_runs(valid, origin):
    """Half-open x ranges with no paired exposure evidence."""
    lost = np.flatnonzero(~valid)
    if not lost.size:
        return [], 0
    return _runs(lost, origin), int(lost.size)


def _runs(index, origin):
    breaks = np.flatnonzero(np.diff(index) > 1)
    starts = np.concatenate(([index[0]], index[breaks + 1]))
    ends = np.concatenate((index[breaks], [index[-1]])) + 1
    return [[int(start) + int(origin), int(end) + int(origin)] for start, end in zip(starts, ends)]


def _summary(s, correction, heights, origin, arrays=True):
    slopes = (s[:-1] + s[1:]) / 2
    raw = np.concatenate(([0.0], np.cumsum(slopes)))
    step = float(correction.mean())
    high_index = int(np.argmax(heights))
    low_index = int(np.argmin(heights))
    guarded = np.abs(correction - step) > TOLERANCE
    return {
        "method": METHOD, "version": VERSION, "status": "closed", "reason": None,
        "gaps": [], "gap_columns": 0, "origin": int(origin),
        "drift": round(float(slopes.sum()), 3), "step": round(step, 4),
        "step_low": round(float(correction.min()), 4), "step_high": round(float(correction.max()), 4),
        "guarded": int(np.count_nonzero(guarded)),
        "guarded_runs": _runs(np.flatnonzero(guarded), origin) if guarded.any() else [],
        "high": round(float(heights[high_index]), 3), "high_x": int(origin) + high_index,
        "high_raw": round(float(raw[high_index]), 3),
        "low": round(float(heights[low_index]), 3), "low_x": int(origin) + low_index,
        "low_raw": round(float(raw[low_index]), 3),
        "heights": [round(float(value), 3) for value in heights] if arrays else None,
        "raw": [round(float(value), 3) for value in raw] if arrays else None,
    }


def close(response, threshold, origin=0, arrays=True):
    """Close one fully evidenced row; heights are in %-pixels from either end.

    A clear run is any column whose response magnitude reaches the adopted
    threshold.  On such a column the correction may not change the slope by more
    than half, so the run keeps both its direction and its order of magnitude;
    the excess moves to the remaining columns and is reported as ``guarded`` -
    the only place this profile is deliberately bent.  If the clear runs cannot
    absorb the whole drift at that limit, the row stays open rather than trading
    real relief for a closed curve.
    """
    s = np.asarray(response, dtype=np.float64)
    if s.size < 2:
        return unavailable("too_short")
    slopes = (s[:-1] + s[1:]) / 2
    drift = float(slopes.sum())
    count = slopes.size
    correction = np.full(count, -drift / count)
    caps = np.where(np.abs(slopes) >= threshold, KEEP * np.abs(slopes), np.inf)
    if np.any(np.abs(correction) > caps + TOLERANCE):
        free = np.ones(count, dtype=bool)
        for _ in range(REDISTRIBUTE_ROUNDS):
            over = np.abs(correction) > caps + TOLERANCE
            if not over.any():
                break
            correction[over] = np.sign(correction[over]) * caps[over]
            free &= ~over
            remaining = -drift - correction.sum()
            if abs(remaining) <= TOLERANCE:
                break
            if not free.any():
                return unavailable("conflict")
            correction[free] += remaining / np.count_nonzero(free)
        if abs(-drift - correction.sum()) > TOLERANCE:
            return unavailable("conflict")
    heights = np.concatenate(([0.0], np.cumsum(slopes + correction)))
    heights[-1] = 0.0
    return _summary(s, correction, heights, origin, arrays)


def row(left, right, settings, calibration, y):
    """Read one left-photo row on the same grid and gain as the zone evidence."""
    from .geometry import common_bounds, read_box
    from .lighting import reliability, signed_response
    from .photo import luminance

    left_size = left.shape[1], left.shape[0]
    zone = read_box(settings["zone"], left_size, "量测框")
    common = common_bounds(left_size, (right.shape[1], right.shape[0]), settings)
    bounds = common.intersect(zone)
    if not bounds.area or not bounds.top <= y < bounds.bottom:
        return None
    top = y - settings["dy"]
    left_row = left[y:y + 1, bounds.left:bounds.right]
    right_row = right[top:top + 1, bounds.left - settings["dx"]:bounds.right - settings["dx"]]
    a, b = luminance(left_row)[0], luminance(right_row)[0]
    valid = reliability(left_row, a[None, :])[0] & reliability(right_row, b[None, :])[0]
    return {
        "bounds": bounds, "zone": zone,
        "response": signed_response(a, b, calibration["gain"]), "valid": valid,
    }


def inspect_row(found, settings, x, arrays=False):
    """Height of one point, closed from the two ends of its own measured row."""
    if found is None:
        return unavailable("outside_zone")
    bounds = found["bounds"]
    if bounds.left != found["zone"].left or bounds.right != found["zone"].right:
        return unavailable("ends_unpaired")
    gaps, counted = gap_runs(found["valid"], bounds.left)
    if counted:
        return unavailable("row_gap", gaps, counted)
    offset = int(x) - bounds.left
    if not 0 <= offset < found["response"].size:
        return unavailable("outside_zone")
    closure = close(found["response"], settings["threshold"], bounds.left)
    if closure["status"] != "closed":
        return closure
    closure["height"] = closure["heights"][offset]
    if not arrays:
        closure["heights"] = None
        closure["raw"] = None
    return closure
