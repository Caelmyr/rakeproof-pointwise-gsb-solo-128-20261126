"""Channel-aware transect rereading and evidence-gated caliber planning.

A *caliber* is the measurement formula used for one continuous part of a
transect.  ``R``/``G``/``B`` are same-name original channels; the default
recommendation plans among those single channels so an adopted column has one
explicit, non-substitutable formula.  The built-in ``gray`` caliber and
multi-channel calibers are also available for hybrid/low-level planning.
"""

from itertools import combinations

import numpy as np

from image_lab.validation import Invalid

from .geometry import common_bounds, read_box, to_right
from .lighting import (
    CLIP_LIMIT,
    DARK_LIMIT,
    GAIN_BOUNDS,
    MIN_FLAT_SUPPORT,
    flat_gain,
    reliability,
    signed_response,
)
from .photo import luminance


CHANNEL_ORDER = ("R", "G", "B")
CHANNEL_INDEX = {name: index for index, name in enumerate(CHANNEL_ORDER)}
CHANNEL_LABELS = {"R": "红通道", "G": "绿通道", "B": "蓝通道"}
CALIBER_ORDER = ("gray", "R", "G", "B")
BRIDGE_RELATIVE_TOLERANCE = 0.35


def channel_reliable(values):
    """Per-channel exposure evidence using the original 15/249 limits."""
    array = np.asarray(values, dtype=np.float32)
    return (array > DARK_LIMIT) & (array < CLIP_LIMIT)


def normalize_channels(value):
    """Return canonical channel names from a string/list/tuple payload."""
    if isinstance(value, str):
        raw = [part.strip() for part in value.replace("|", ",").replace("+", ",").split(",")]
    elif isinstance(value, (list, tuple)):
        raw = []
        for item in value:
            raw.extend(str(item).replace("+", ",").split(","))
    else:
        raise Invalid("通道口径必须是 R/G/B 或其列表")
    names = []
    for item in raw:
        name = str(item).strip().upper()
        if name in {"RED", "红", "红通道"}:
            name = "R"
        elif name in {"GREEN", "绿", "绿通道"}:
            name = "G"
        elif name in {"BLUE", "蓝", "蓝通道"}:
            name = "B"
        if name not in CHANNEL_INDEX:
            raise Invalid("通道口径只支持 R、G、B")
        if name not in names:
            names.append(name)
    if not names:
        raise Invalid("通道口径至少需要一个通道")
    return tuple(name for name in CHANNEL_ORDER if name in names)


def caliber_key(value):
    if value is None:
        return None
    if isinstance(value, str):
        token = value.strip()
        if token.lower() in {"gray", "grey", "luminance", "灰度", "原灰度"}:
            return "gray"
        return "+".join(normalize_channels(token))
    if isinstance(value, (list, tuple)):
        if len(value) == 1 and str(value[0]).lower() in {"gray", "luminance"}:
            return "gray"
        return "+".join(normalize_channels(value))
    raise Invalid("无法识别通道口径")


def caliber_channels(key):
    if key is None or key == "gray":
        return None
    return tuple(key.split("+"))


def all_channel_calibers():
    result = []
    for count in range(1, 4):
        for items in combinations(CHANNEL_ORDER, count):
            result.append("+".join(items))
    return result


def _calibration_record(name, gain, support, state, usable=None):
    record = {
        "channel": name,
        "label": CHANNEL_LABELS.get(name, "原灰度"),
        "gain": round(float(gain), 6),
        "support": int(support),
        "state": state,
        "flat_supported": int(support) >= MIN_FLAT_SUPPORT,
    }
    record["usable"] = bool(usable if usable is not None else state == "applied")
    return record


def channel_flat_calibration(left, right, settings, common=None):
    """Estimate one right-photo gain per channel from the shared flat box.

    Every median is formed only from pixels where that same-name channel is
    exposed in *both* originals.  When balance is disabled the gain is 1, but
    the paired support is still reported for audit.
    """
    left_size = left.shape[1], left.shape[0]
    right_size = right.shape[1], right.shape[0]
    if common is None:
        common = common_bounds(left_size, right_size, settings)
    patch = common.intersect(read_box(settings["flat"], left_size, "平面参照框"))
    result = {}
    balance = bool(settings.get("balance"))
    for name in CHANNEL_ORDER:
        index = CHANNEL_INDEX[name]
        if not patch.area:
            state = "off" if not balance else "insufficient"
            result[name] = _calibration_record(name, 1.0, 0, state, usable=not balance)
            continue
        lp = left[patch.slices][..., index].astype(np.float32)
        rp = right[to_right(patch, settings).slices][..., index].astype(np.float32)
        valid = channel_reliable(lp) & channel_reliable(rp)
        support = int(np.count_nonzero(valid))
        if not balance:
            result[name] = _calibration_record(name, 1.0, support, "off", usable=True)
            continue
        if support < MIN_FLAT_SUPPORT:
            result[name] = _calibration_record(name, 1.0, support, "insufficient", usable=False)
            continue
        ratio = float(np.median(lp[valid] / rp[valid]))
        if not GAIN_BOUNDS[0] <= ratio <= GAIN_BOUNDS[1]:
            result[name] = _calibration_record(name, 1.0, support, "outside_range", usable=False)
        else:
            result[name] = _calibration_record(name, ratio, support, "applied", usable=True)
    return result


def channel_value(left_values, right_values, channels, gains):
    """Contrast for an explicit channel tuple; arrays are last-axis RGB."""
    chosen = normalize_channels(channels) if not isinstance(channels, tuple) else channels
    left_num = np.zeros(left_values.shape[:-1], dtype=np.float32)
    right_num = np.zeros(right_values.shape[:-1], dtype=np.float32)
    for name in chosen:
        index = CHANNEL_INDEX[name]
        gain = float(gains[name]["gain"] if isinstance(gains.get(name), dict) else gains[name])
        left_num += left_values[..., index].astype(np.float32)
        right_num += right_values[..., index].astype(np.float32) * gain
    count = float(len(chosen))
    left_num /= count
    right_num /= count
    return signed_response(left_num, right_num, 1.0)


def point_channel_reading(left_rgb, right_rgb, channels, calibration):
    """Read one paired pixel without substituting another channel."""
    chosen = normalize_channels(channels) if not isinstance(channels, tuple) else channels
    left_values = np.asarray(left_rgb, dtype=np.float32)
    right_values = np.asarray(right_rgb, dtype=np.float32)
    checks = {}
    missing = []
    gain = {}
    for name in chosen:
        index = CHANNEL_INDEX[name]
        left_ok = bool(channel_reliable(left_values[index]))
        right_ok = bool(channel_reliable(right_values[index]))
        calib = calibration.get(name, {})
        calib_ok = bool(calib.get("usable", not bool(calibration)))
        checks[name] = {
            "left_valid": left_ok,
            "right_valid": right_ok,
            "calibration_valid": calib_ok,
            "valid": left_ok and right_ok and calib_ok,
            "left_value": round(float(left_values[index]), 2),
            "right_value": round(float(right_values[index]), 2),
            "gain": calib.get("gain", 1.0),
            "calibration_state": calib.get("state", "unknown"),
        }
        if not checks[name]["valid"]:
            missing.append(name)
        gain[name] = calib.get("gain", 1.0)
    if missing:
        return {
            "channels": list(chosen), "valid": False, "response": None,
            "missing_channels": missing, "checks": checks,
        }
    response = float(channel_value(left_values, right_values, chosen, gain))
    return {
        "channels": list(chosen), "valid": True, "response": round(response, 3),
        "missing_channels": [], "checks": checks,
    }


def _empty_section(settings):
    return {
        "y": settings["scan_y"], "origin": None, "length": 0,
        "calibrations": {}, "samples": [], "recommendation": None,
        "plan": None, "suggestion": None, "recommended_adoption": None,
        "channel_recommendation": None, "channel_plan": None,
        "hybrid_recommendation": None, "gray_bridge_recommendation": None,
        "set_recommendation": None, "advice": None,
    }


def channel_section(left, right, settings, common=None, bounds=None, evidence=None,
                    calibration=None):
    """Build channel evidence and route recommendations for the adopted scan."""
    left_size = left.shape[1], left.shape[0]
    right_size = right.shape[1], right.shape[0]
    if common is None:
        common = common_bounds(left_size, right_size, settings)
    zone = read_box(settings["zone"], left_size, "量测框")
    if bounds is None:
        bounds = common.intersect(zone)
    y = int(settings["scan_y"])
    calibration = channel_flat_calibration(left, right, settings, common) if calibration is None else calibration
    gray_calibration = flat_gain(left, right, settings, common)

    if not bounds.area or not bounds.top <= y < bounds.bottom:
        result = _empty_section(settings)
        result["calibrations"] = calibration
        result["gray_calibration"] = gray_calibration
        return result

    row = y - bounds.top
    n = bounds.width
    xs = np.arange(bounds.left, bounds.right, dtype=np.int32)
    right_x = xs - int(settings["dx"])
    right_y = y - int(settings["dy"])
    left_rgb = left[y, bounds.left:bounds.right, :].astype(np.float32)
    right_rgb = right[right_y, bounds.left - int(settings["dx"]):bounds.right - int(settings["dx"]), :].astype(np.float32)

    gray_a = luminance(left_rgb)
    gray_b = luminance(right_rgb)
    gray_valid = reliability(left_rgb, gray_a) & reliability(right_rgb, gray_b)
    gray_values = signed_response(gray_a, gray_b, gray_calibration["gain"])

    feasible = {"gray": gray_valid}
    values = {"gray": gray_values}
    headroom = {"gray": _rgb_headroom(left_rgb, right_rgb)}
    raw_channel_values = {}
    channel_masks = {}

    for name in CHANNEL_ORDER:
        index = CHANNEL_INDEX[name]
        lv = left_rgb[:, index]
        rv = right_rgb[:, index]
        raw_channel_values[name] = signed_response(lv, rv, calibration[name]["gain"])
        pixel_ok = channel_reliable(lv) & channel_reliable(rv)
        channel_masks[name] = pixel_ok
        feasible[name] = pixel_ok & calibration[name]["usable"]
        values[name] = raw_channel_values[name]
        headroom[name] = np.minimum.reduce([
            lv - DARK_LIMIT, (CLIP_LIMIT - 1) - lv,
            rv - DARK_LIMIT, (CLIP_LIMIT - 1) - rv,
        ])

    # Every non-empty RGB subset is available as an explicit caliber.
    for key in all_channel_calibers():
        names = tuple(key.split("+"))
        if len(names) == 1:
            continue
        mask = np.ones(n, dtype=bool)
        nums_a = np.zeros(n, dtype=np.float32)
        nums_b = np.zeros(n, dtype=np.float32)
        room = np.full(n, 255.0, dtype=np.float32)
        for name in names:
            mask &= feasible[name]
            idx = CHANNEL_INDEX[name]
            gain = calibration[name]["gain"]
            nums_a += left_rgb[:, idx]
            nums_b += right_rgb[:, idx] * gain
            room = np.minimum(room, headroom[name])
        nums_a /= len(names)
        nums_b /= len(names)
        feasible[key] = mask
        values[key] = signed_response(nums_a, nums_b, 1.0)
        headroom[key] = room

    original_valid = gray_valid if evidence is None else evidence.valid[row]
    original_values = gray_values if evidence is None else evidence.response[row]

    samples = []
    for j, x in enumerate(xs):
        available = [key for key in CALIBER_ORDER if bool(feasible[key][j])]
        sample = {
            "x": int(x),
            "right_x": int(right_x[j]),
            "right_y": int(right_y),
            "original_valid": bool(original_valid[j]),
            "original_value": _round_value(original_values[j], original_valid[j]),
            "gray_valid": bool(gray_valid[j]),
            "gray_value": _round_value(gray_values[j], gray_valid[j]),
            "available_calibers": available,
            "channels": {},
        }
        for name in CHANNEL_ORDER:
            sample["channels"][name] = {
                "valid": bool(channel_masks[name][j]),
                "calibration_valid": bool(calibration[name]["usable"]),
                "left_value": round(float(left_rgb[j, CHANNEL_INDEX[name]]), 2),
                "right_value": round(float(right_rgb[j, CHANNEL_INDEX[name]]), 2),
                "value": _round_value(values[name][j], feasible[name][j]),
            }
        sample["valid_channels"] = [name for name in CHANNEL_ORDER if bool(channel_masks[name][j])]
        sample["available_channels"] = [name for name in CHANNEL_ORDER if bool(feasible[name][j])]
        sample["channel_values"] = {
            name: sample["channels"][name]["value"] for name in CHANNEL_ORDER
        }
        samples.append(sample)

    threshold = float(settings["threshold"])
    hybrid_recommendation = plan_route(
        feasible, values, headroom, xs, threshold,
        candidate_keys=CALIBER_ORDER,
        bridge_tolerance=None,
    )
    channel_recommendation = plan_route(
        feasible, values, headroom, xs, threshold,
        candidate_keys=CHANNEL_ORDER,
        bridge_tolerance=None,
    )
    set_recommendation = plan_route(
        feasible, values, headroom, xs, threshold,
        candidate_keys=("R", "G", "B", "R+G", "R+B", "G+B", "R+G+B"),
        bridge_tolerance=None,
    )
    # The primary recommendation uses one explicit channel caliber.  Set-based
    # calibers remain available as set_recommendation for callers that want an
    # average of several jointly valid channels.
    recommendation = channel_recommendation
    _attach_samples(samples, recommendation, values, feasible)

    readable = int(np.count_nonzero(np.any(np.column_stack([feasible[k] for k in CALIBER_ORDER]), axis=1)))
    channel_readable = int(np.count_nonzero(np.any(np.column_stack([feasible[k] for k in CHANNEL_ORDER]), axis=1)))
    recovered = sum(
        1 for sample in samples
        if sample["recommended_caliber"] not in (None, "gray") and not sample["original_valid"]
    )
    result = {
        "y": y,
        "origin": int(bounds.left),
        "end": int(bounds.right - 1),
        "length": n,
        "right_origin_x": int(bounds.left - settings["dx"]),
        "right_y": right_y,
        "threshold": threshold,
        "calibrations": calibration,
        "gray_calibration": gray_calibration,
        "samples": samples,
        "recommendation": recommendation,
        "plan": recommendation,
        "suggestion": recommendation,
        "recommended_adoption": recommendation["adoption"],
        "channel_recommendation": channel_recommendation,
        "channel_plan": channel_recommendation,
        "single_channel_recommendation": channel_recommendation,
        "hybrid_recommendation": hybrid_recommendation,
        "gray_bridge_recommendation": hybrid_recommendation,
        "set_recommendation": set_recommendation,
        "counts": {
            "columns": n,
            "original_valid": int(np.count_nonzero(original_valid)),
            "readable": readable,
            "channel_readable": channel_readable,
            "recovered": recovered,
            "recovered_columns": recovered,
            "recovered_length": recovered,
            "saved_length": recovered,
            "preserved_length": recovered,
            "missing": n - readable,
        },
    }
    advice = {
        "policy": "max_read_columns_then_fewest_breaks_and_switches",
        "read_columns": recommendation["counts"]["adopted"],
        "total_columns": n,
        "original_valid_columns": int(np.count_nonzero(original_valid)),
        "recovered_length": recovered,
        "preserved_length": recovered,
        "longest_continuous_columns": recommendation["counts"]["longest_columns"],
        "switch_count": recommendation["counts"]["switches"],
        "switch_at_x": [item.get("at_x", item.get("bridge_x")) for item in recommendation["switches"]],
        "bridge_x": [item["bridge_x"] for item in recommendation["switches"]],
        "breaks_at_x": [item["at_x"] for item in recommendation["breaks"]],
        "message": (
            f"建议沿用同一通道口径读完 {recommendation['counts']['adopted']}/{n} 列，"
            f"从原灰度断点保住 {recovered}px；"
            f"换口径 {recommendation['counts']['switches']} 次，"
            f"断开 {recommendation['counts']['breaks']} 次。"
        ),
    }
    result["advice"] = advice
    recommendation["advice"] = advice
    return result


def _rgb_headroom(left_rgb, right_rgb):
    return np.minimum.reduce([
        left_rgb.min(axis=1) - DARK_LIMIT,
        (CLIP_LIMIT - 1) - left_rgb.max(axis=1),
        right_rgb.min(axis=1) - DARK_LIMIT,
        (CLIP_LIMIT - 1) - right_rgb.max(axis=1),
    ])


def _round_value(value, valid):
    if not valid or not np.isfinite(value):
        return None
    return round(float(value), 3)


def bridge_evidence(key_from, key_to, j, feasible, values, xs, threshold, tolerance=None):
    """Check same-column calculability, numerical continuity, and sign."""
    if key_from == key_to:
        return None
    if not bool(feasible[key_from][j]) or not bool(feasible[key_to][j]):
        return {"connected": False, "reason": "same_column_caliber_missing"}
    a = float(values[key_from][j])
    b = float(values[key_to][j])
    if not np.isfinite(a) or not np.isfinite(b) or a == 0 or b == 0 or np.sign(a) != np.sign(b):
        return {"connected": False, "reason": "slope_direction_conflict"}
    limit = float(tolerance) if tolerance is not None else max(
        threshold, BRIDGE_RELATIVE_TOLERANCE * max(abs(a), abs(b))
    )
    difference = abs(a - b)
    if difference > limit + 1e-9:
        return {"connected": False, "reason": "contrast_mismatch"}
    direction = 1 if a > 0 else -1
    classified = (
        (a >= threshold and b >= threshold)
        or (a <= -threshold and b <= -threshold)
    )
    return {
        "connected": True,
        "x": int(xs[j]),
        "bridge_x": int(xs[j]),
        "column_index": int(j),
        "from": key_from,
        "to": key_to,
        "from_value": round(a, 3),
        "to_value": round(b, 3),
        "difference": round(difference, 3),
        "tolerance": round(limit, 3),
        "direction": direction,
        "same_threshold_direction": bool(classified),
    }


def plan_route(feasible, values, headroom, xs, threshold, candidate_keys=CALIBER_ORDER,
               bridge_tolerance=None):
    """Choose a max-read route, then minimize breaks and caliber switches."""
    n = len(xs)
    if n == 0:
        return _empty_plan(candidate_keys)
    dp = [{} for _ in range(n)]
    parents = [{} for _ in range(n)]
    overall = [None] * n

    for j in range(n):
        for key in candidate_keys:
            if key not in feasible or not bool(feasible[key][j]):
                continue
            quality = float(headroom[key][j]) if headroom is not None else 0.0
            preference = 1.0 if key == "gray" else 0.0
            breadth = 0 if key == "gray" else len(key.split("+"))
            best = None
            best_parent = None

            def consider(metrics, parent, edge):
                nonlocal best, best_parent
                candidate = _rank(metrics)
                if best is None or candidate > _rank(best):
                    best = metrics
                    best_parent = (parent, edge)

            if j > 0 and key in dp[j - 1]:
                prev = dp[j - 1][key]
                consider((prev[0] + 1, prev[1], prev[2], prev[3],
                          prev[4] + preference, prev[5] + quality,
                          prev[6] + breadth),
                         (j - 1, key), "continue")
            if j > 0:
                for old_key, prev in dp[j - 1].items():
                    if old_key == key:
                        continue
                    # The bridge is the last shared column: old_key is adopted
                    # there, while key must also be independently calculable.
                    # The new caliber starts at the current column.
                    bridge = bridge_evidence(
                        old_key, key, j - 1, feasible, values, xs, threshold, bridge_tolerance
                    )
                    if bridge["connected"]:
                        bridge = dict(bridge)
                        bridge["at_x"] = int(xs[j])
                        bridge["after_x"] = int(xs[j - 1])
                        bridge["switch_x"] = int(xs[j])
                        bridge["start_x"] = int(xs[j])
                        consider((prev[0] + 1, prev[1], prev[2] + 1, prev[3] + j,
                                  prev[4] + preference, prev[5] + quality,
                                  prev[6] + breadth),
                                (j - 1, old_key), bridge)
            prior = overall[j - 1] if j > 0 else None
            if prior is not None:
                pm = prior["metrics"]
                consider((pm[0] + 1, pm[1] + (1 if pm[0] > 0 else 0), pm[2], pm[3],
                          pm[4] + preference, pm[5] + quality, pm[6] + breadth),
                         (prior["end_index"], prior["end_key"]), "break")
            else:
                consider((1, 0, 0, 0.0, preference, quality, breadth), None, "start")

            if best is not None:
                dp[j][key] = best
                parents[j][key] = best_parent

        if dp[j]:
            end_key = max(dp[j], key=lambda item: _rank(dp[j][item]))
            overall[j] = {"end_index": j, "end_key": end_key, "metrics": dp[j][end_key]}
        else:
            overall[j] = overall[j - 1] if j > 0 else None

    terminal = overall[-1]
    if terminal is None or terminal["metrics"][0] == 0:
        return _empty_plan(candidate_keys)

    chosen = [None] * n
    j = terminal["end_index"]
    key = terminal["end_key"]
    while j is not None:
        chosen[j] = key
        parent, edge = parents[j][key]
        if parent is None:
            break
        pj, pkey = parent
        j, key = pj, pkey

    switches = []
    breaks = []
    segments = []
    segment_start = None
    segment_key = None
    previous_index = None
    previous_key = None

    def close_segment(end_exclusive):
        if segment_start is None:
            return
        x0 = int(xs[segment_start])
        x1 = int(xs[end_exclusive - 1])
        segments.append({
            "caliber": segment_key,
            "channels": caliber_channels(segment_key),
            "start_index": int(segment_start),
            "end_index": int(end_exclusive),
            "start_x": x0,
            "end_x_exclusive": int(xs[end_exclusive - 1] + 1),
            "end_x": x1,
            "columns": int(end_exclusive - segment_start),
            "length": int(x1 - x0 + 1),
        })

    for j, key in enumerate(chosen):
        if key is None:
            continue
        if segment_start is None:
            segment_start, segment_key = j, key
        elif j != previous_index + 1:
            close_segment(previous_index + 1)
            breaks.append({
                "after_x": int(xs[previous_index]), "at_x": int(xs[j]),
                "from": previous_key, "to": key, "reason": "uncovered_gap",
            })
            segment_start, segment_key = j, key
        elif key != previous_key:
            bridge = bridge_evidence(
                previous_key, key, j - 1, feasible, values, xs, threshold, bridge_tolerance
            )
            if bridge["connected"]:
                bridge = dict(bridge)
                bridge["at_x"] = int(xs[j])
                bridge["after_x"] = int(xs[j - 1])
                bridge["switch_x"] = int(xs[j])
                bridge["start_x"] = int(xs[j])
                switches.append(bridge)
                close_segment(j)
                segment_start, segment_key = j, key
            else:
                breaks.append({
                    "after_x": int(xs[j - 1]), "at_x": int(xs[j]),
                    "from": previous_key, "to": key,
                    "reason": bridge.get("reason", "no_bridge"),
                })
                close_segment(j)
                segment_start, segment_key = j, key
        previous_index = j
        previous_key = key
    if previous_index is not None:
        close_segment(previous_index + 1)

    longest = max(segments, key=lambda item: item["columns"], default=None)
    metrics = terminal["metrics"]
    plan = {
        "strategy": "max_read_then_connected_min_switch_v1",
        "candidate_calibers": list(candidate_keys),
        "caliber_by_index": [None if key is None else str(key) for key in chosen],
        "caliber_by_x": {
            int(xs[j]): chosen[j] for j in range(n) if chosen[j] is not None
        },
        "segments": segments,
        "switches": switches,
        "breaks": breaks,
        "counts": {
            "adopted": int(metrics[0]),
            "breaks": int(metrics[1]),
            "switches": int(metrics[2]),
            "segments": len(segments),
            "longest_columns": None if longest is None else int(longest["columns"]),
            "longest_start_x": None if longest is None else longest["start_x"],
            "longest_end_x": None if longest is None else longest["end_x"],
            "longest_length": None if longest is None else longest["length"],
        },
    }
    plan["adoption"] = adoption_from_plan(plan, settings=None, strategy=plan["strategy"])
    return plan


def _rank(metrics):
    reads, breaks, switches, switch_timing, preference, quality, breadth = metrics
    return (reads, -breaks, -switches, round(switch_timing, 6),
            round(preference, 6), round(quality, 6), round(breadth, 6))


def _empty_plan(candidate_keys):
    return {
        "strategy": "max_read_then_connected_min_switch_v1",
        "candidate_calibers": list(candidate_keys),
        "caliber_by_index": [], "caliber_by_x": {}, "segments": [],
        "switches": [], "breaks": [],
        "counts": {"adopted": 0, "breaks": 0, "switches": 0, "segments": 0,
                   "longest_columns": None, "longest_start_x": None,
                   "longest_end_x": None, "longest_length": None},
        "adoption": {"version": 1, "caliber_by_x": {}, "global_caliber": None},
    }


def _attach_samples(samples, recommendation, values=None, feasible=None):
    by_x = recommendation["caliber_by_x"]
    xs = recommendation.get("_xs")
    for index, sample in enumerate(samples):
        key = by_x.get(sample["x"])
        sample["recommended_caliber"] = key
        sample["recommended_channels"] = caliber_channels(key)
        if key is None:
            sample["recommended_value"] = None
        elif key == "gray":
            sample["recommended_value"] = sample["gray_value"]
        elif "+" not in key:
            sample["recommended_value"] = sample["channels"][key]["value"]
        elif values is not None and key in values:
            valid = True if feasible is None else bool(feasible[key][index])
            sample["recommended_value"] = _round_value(values[key][index], valid)
        else:
            sample["recommended_value"] = None


def fingerprint(settings):
    keys = ("zone", "flat", "dx", "dy", "scan_y", "balance", "threshold")
    return {key: settings[key] for key in keys}


def adoption_from_plan(plan, settings=None, strategy=None):
    caliber_by_x = {str(x): key for x, key in plan.get("caliber_by_x", {}).items()}
    channels_by_x = {
        x: caliber_channels(key) for x, key in caliber_by_x.items() if key != "gray"
    }
    return {
        "version": 1,
        "strategy": strategy or plan.get("strategy"),
        "fingerprint": fingerprint(settings) if settings is not None else None,
        "global_caliber": None,
        "caliber_by_x": caliber_by_x,
        "channels_by_x": channels_by_x,
        "segments": plan.get("segments", []),
        "switches": plan.get("switches", []),
        "counts": plan.get("counts", {}),
    }


def normalize_adoption(raw, settings=None):
    """Validate an explicit or persisted adoption payload."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = {"caliber": raw}
    if not isinstance(raw, dict):
        raise Invalid("通道采用方案必须是对象")

    global_caliber = None
    for field in (
        "global_caliber", "caliber", "channel", "channels",
        "adopted_channel", "adopted_channels", "adopted_caliber",
        "channel_caliber", "selected_channel", "channel_choice", "active_channel",
        "channel_mode", "channel_policy", "reread_channel", "section_channel",
    ):
        if raw.get(field) is not None:
            global_caliber = raw[field]
            break
    if global_caliber is not None:
        global_caliber = caliber_key(global_caliber)

    source_map = raw.get("caliber_by_x", raw.get("columns", raw.get("channels_by_x")))
    caliber_by_x = {}
    if source_map is not None:
        if not isinstance(source_map, dict):
            raise Invalid("按原列保存的通道口径必须是对象")
        for x, value in source_map.items():
            try:
                x_int = int(x)
            except (TypeError, ValueError) as exc:
                raise Invalid("通道口径的原列编号无效") from exc
            if isinstance(value, dict):
                value = value.get("caliber", value.get("channels", value.get("channel")))
            key = caliber_key(value)
            caliber_by_x[str(x_int)] = key

    plan = raw.get("plan")
    if isinstance(plan, dict) and not caliber_by_x:
        caliber_by_x = {str(x): key for x, key in plan.get("caliber_by_x", {}).items()}

    if not global_caliber and not caliber_by_x:
        raise Invalid("没有可采用的通道口径")

    adoption = {
        "version": 1,
        "strategy": str(raw.get("strategy") or "explicit_caliber_v1")[:80],
        "fingerprint": fingerprint(settings) if settings is not None else raw.get("fingerprint"),
        "global_caliber": global_caliber,
        "caliber_by_x": caliber_by_x,
        "channels_by_x": {
            x: caliber_channels(key) for x, key in caliber_by_x.items() if key != "gray"
        },
    }
    for optional in ("segments", "switches", "counts", "recommendation"):
        if optional in raw:
            adoption[optional] = raw[optional]
        elif isinstance(plan, dict) and optional in plan:
            adoption[optional] = plan[optional]
    return adoption


def adoption_matches(adoption, settings):
    if not isinstance(adoption, dict):
        return False
    saved = adoption.get("fingerprint")
    return saved == fingerprint(settings)


def resolve_adopted_caliber(adoption, x, query=None, anchor_x=None):
    """Resolve one exact caliber; never search for a substitute."""
    if query:
        return caliber_key(query), "query", int(x)
    if not isinstance(adoption, dict):
        return None, "no_adoption", int(x)
    if adoption.get("global_caliber"):
        return str(adoption["global_caliber"]), "adoption", int(x)
    caliber_column = int(anchor_x) if anchor_x is not None else int(x)
    key = adoption.get("caliber_by_x", {}).get(str(caliber_column))
    if key is None:
        channels = adoption.get("channels_by_x", {}).get(str(caliber_column))
        if channels:
            key = caliber_key(channels)
    if key is None:
        return None, "no_adopted_column", caliber_column
    source = "adoption_anchor" if anchor_x is not None and int(anchor_x) != int(x) else "adoption"
    return str(key), source, caliber_column


# Descriptive aliases for callers/tests that name the flat-patch step as a gain.
flat_channel_gain = channel_flat_calibration
channel_gains = channel_flat_calibration
