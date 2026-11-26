"""Track one opposite-light groove pair through consecutive original rows.

The left wall of a pressed groove is relatively bright in the right-direction
photograph, hence a negative signed response.  The right wall is relatively
bright in the left photograph, hence a positive response.  Tracking keeps the
two peaks as one coupled pair so a single wall cannot leap to a neighbouring
groove.
"""

from __future__ import annotations

import math
from copy import deepcopy

import numpy as np

from image_lab.validation import Invalid, integer, object_value, text


PEAK_FLOOR = 4.0
MAX_GAP_ROWS = 3
BEAM_WIDTH = 64
SCHEMA = "rakeproof.groove-track/v1"


def _round(value, digits=3):
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _peak_candidates(evidence, y, sign, min_contrast=PEAK_FLOOR):
    """Return non-maximum-suppressed signed peaks in original coordinates."""
    bounds = evidence.bounds
    if not bounds.top <= y < bounds.bottom or bounds.width < 3:
        return []
    row = y - bounds.top
    response = evidence.response[row]
    valid = evidence.valid[row]
    radius = 2
    raw = []
    for offset in range(bounds.width):
        value = float(response[offset])
        if not valid[offset] or abs(value) < min_contrast:
            continue
        if sign < 0 and value >= 0:
            continue
        if sign > 0 and value <= 0:
            continue
        lo = max(0, offset - radius)
        hi = min(bounds.width, offset + radius + 1)
        window_response = response[lo:hi]
        window_valid = valid[lo:hi]
        if sign > 0:
            # Invalid clipped/dark pixels must not hide the strongest usable
            # shoulder immediately before an evidence gap.
            window = np.where(window_valid, window_response, -math.inf)
            extreme = float(np.max(window))
        else:
            window = np.where(window_valid, window_response, math.inf)
            extreme = float(np.min(window))
        # The centre is one of the local extrema.  Ties are resolved by NMS.
        if abs(value - extreme) > 1e-6:
            continue
        x = offset + bounds.left
        raw.append({
            "x": x,
            "y": y,
            "sign": sign,
            "value": _round(value),
            "strength": abs(value),
            "valid": True,
        })
    raw.sort(key=lambda item: item["strength"], reverse=True)
    peaks = []
    for peak in raw:
        # Same-wall duplicates inside one broad slope are one source segment.
        # Seven pixels also merges a threshold split around one broad peak.
        if all(abs(peak["x"] - old["x"]) >= 7 for old in peaks):
            peaks.append(peak)
    peaks.sort(key=lambda item: item["x"])
    return peaks


def _pairs_for_row(evidence, y, max_width, min_contrast=PEAK_FLOOR):
    lefts = _peak_candidates(evidence, y, -1, min_contrast)
    rights = _peak_candidates(evidence, y, 1, min_contrast)
    pairs = []
    for left in lefts:
        for right in rights:
            width = right["x"] - left["x"]
            if width < 2 or width > max_width:
                continue
            # A second right-light (negative) wall between the pair marks the
            # left wall of a neighbouring groove; never span across it.
            intervening_left = any(
                left["x"] < other["x"] < right["x"]
                and other["strength"] >= max(7.0, 0.45 * left["strength"])
                for other in lefts
                if other["x"] != left["x"]
            )
            if intervening_left:
                continue
            # An earlier substantial left-light peak is the natural right wall;
            # do not reach across it to a later ridge or groove slope.
            intervening_right = any(
                left["x"] < other["x"] < right["x"] - 11
                and other["strength"] >= max(7.0, 0.45 * right["strength"])
                for other in rights
                if other["x"] != right["x"]
            )
            if intervening_right:
                continue
            weak = min(left["strength"], right["strength"])
            pairs.append(_make_pair(y, left, right, width, weak))
    pairs.sort(key=lambda pair: pair["score"], reverse=True)
    return pairs


def _make_pair(y, left, right, width=None, weak=None):
    width = int(right["x"] - left["x"]) if width is None else width
    weak = min(left["strength"], right["strength"]) if weak is None else weak
    balance = -abs(left["strength"] - right["strength"]) * 0.08
    broad_penalty = max(0, width - 75) * 0.12
    score = 3.0 * weak + 0.35 * (left["strength"] + right["strength"]) + balance - broad_penalty
    return {
        "y": y,
        "left_x": int(left["x"]),
        "right_x": int(right["x"]),
        "width": int(width),
        "left_value": _round(left["value"]),
        "right_value": _round(right["value"]),
        "weak_contrast": _round(weak),
        "score": _round(score, 4),
        "left_peak": left,
        "right_peak": right,
    }


def _seed_pair(evidence, x, y):
    bounds = evidence.bounds
    max_width = max(48, min(260, bounds.width // 2 if bounds.width else 260))
    pairs = _pairs_for_row(evidence, y, max_width)
    if not pairs:
        raise Invalid("这一行没有找到左照相对亮与右照相对亮的一对可用坡峰")

    def click_distance(pair):
        if pair["left_x"] <= x <= pair["right_x"]:
            return 0.0
        return float(min(abs(x - pair["left_x"]), abs(x - pair["right_x"])))

    choices = []
    for pair in pairs:
        distance = click_distance(pair)
        width = pair["width"]
        width_prior = -max(0, width - 90) * 0.25 - max(0, 5 - width) * 0.8
        choice_score = pair["score"] - 2.2 * distance + width_prior
        choices.append((choice_score, distance, pair))
    choices.sort(key=lambda item: item[0], reverse=True)
    best_score, distance, pair = choices[0]
    # A click well outside every opposite-sign pair is probably not this groove.
    if distance > 24 and best_score < 35:
        raise Invalid("请点在一条沟的两道坡之间，或直接点在它的左蓝、右橙坡峰上")
    return pair


def _source_records(pair, settings):
    """Attach native right-photo coordinates to both adopted source peaks."""
    result = {}
    for side in ("left", "right"):
        peak = pair[f"{side}_peak"]
        x, y = peak["x"], peak["y"]
        result[side] = {
            "side": side,
            "x": int(x),
            "y": int(y),
            "right_x": int(x - settings["dx"]),
            "right_y": int(y - settings["dy"]),
            "response": peak["value"],
            "valid": True,
        }
    return result["left"], result["right"]


def _measured_row(pair, settings):
    left_source, right_source = _source_records(pair, settings)
    return {
        "y": int(pair["y"]),
        "measured": True,
        "gap": False,
        "width": int(pair["width"]),
        "left_x": int(pair["left_x"]),
        "right_x": int(pair["right_x"]),
        "left_contrast": pair["left_value"],
        "right_contrast": pair["right_value"],
        "weak_contrast": pair["weak_contrast"],
        "left_source": left_source,
        "right_source": right_source,
    }


def _missing_row(evidence, y, last_left, last_right):
    """Create a blank-width row and distinguish a short dark patch from no match."""
    left = evidence.bounds.left
    top = evidence.bounds.top
    row = y - top
    dark = False
    if 0 <= row < evidence.valid.shape[0]:
        obscured_walls = []
        for expected_x in (last_left, last_right):
            centre = expected_x - left
            lo = max(0, centre - 3)
            hi = min(evidence.valid.shape[1], centre + 4)
            obscured_walls.append(hi <= lo or not bool(evidence.valid[row, lo:hi].all()))
        dark = any(obscured_walls)
    return {
        "y": int(y),
        "measured": False,
        "gap": True,
        "width": None,
        "left_x": None,
        "right_x": None,
        "left_contrast": None,
        "right_contrast": None,
        "weak_contrast": None,
        "left_source": None,
        "right_source": None,
        "reason": "dark_spot" if dark else "unmatched",
    }


def _reconstruct(node):
    rows = []
    while node is not None:
        rows.append(node["row"])
        node = node["parent"]
    rows.reverse()
    # An unresolved trailing gap is not an adopted continuation.
    while rows and not rows[-1]["measured"]:
        rows.pop()
    return rows


def _track_direction(evidence, settings, seed, direction=1):
    bounds = evidence.bounds
    if direction >= 0:
        ys = range(seed["y"] + 1, bounds.bottom)
    else:
        ys = range(seed["y"] - 1, bounds.top - 1, -1)

    seed_row = _measured_row(seed, settings)
    root = {"row": seed_row, "parent": None}
    width = seed["width"]
    max_width = max(64, min(280, int(max(width * 2.5, width + 40))))
    hyp = {
        "left_x": seed["left_x"], "right_x": seed["right_x"],
        "left_value": seed["left_peak"]["value"], "right_value": seed["right_peak"]["value"],
        "width": width, "velocity_l": 0.0, "velocity_r": 0.0,
        "last_y": seed["y"], "gap": 0, "score": float(seed["score"]) + 8.0,
        "node": root,
    }
    beam = [hyp]
    finished = []

    for y in ys:
        next_beam = []
        candidates = _pairs_for_row(evidence, y, max_width)
        for state in beam:
            delta_y = abs(y - state["last_y"])
            pred_l = state["left_x"] + state["velocity_l"] * delta_y * direction
            pred_r = state["right_x"] + state["velocity_r"] * delta_y * direction
            transitions = []
            jump_radius = 7.0 + 2.5 * max(0, delta_y - 1)
            width_gate = max(7.0, 0.38 * state["width"]) + 2.0 * max(0, delta_y - 1)
            for pair in candidates:
                dl = pair["left_x"] - pred_l
                dr = pair["right_x"] - pred_r
                # Both walls must remain near their own predicted trajectories.
                if abs(dl) > jump_radius or abs(dr) > jump_radius:
                    continue
                width_change = pair["width"] - state["width"]
                if abs(width_change) > width_gate:
                    continue
                # Do not accept a pair whose ordering has effectively crossed.
                if pair["width"] < 2:
                    continue
                movement_penalty = 0.075 * (dl * dl + dr * dr)
                width_penalty = 0.10 * width_change * width_change / max(4.0, state["width"])
                reward = 8.0 + 2.15 * pair["weak_contrast"] + 0.22 * (
                    abs(pair["left_value"]) + abs(pair["right_value"])
                )
                score = state["score"] + reward - movement_penalty - width_penalty
                old_delta = max(1, abs(pair["y"] - state["last_y"]))
                vel_l = (pair["left_x"] - state["left_x"]) / old_delta
                vel_r = (pair["right_x"] - state["right_x"]) / old_delta
                # Smooth velocity to avoid a one-row false peak steering later rows.
                if state["gap"] == 0:
                    vel_l = 0.55 * state["velocity_l"] + 0.45 * vel_l
                    vel_r = 0.55 * state["velocity_r"] + 0.45 * vel_r
                row = _measured_row(pair, settings)
                node = {"row": row, "parent": state["node"]}
                transitions.append({
                    "left_x": pair["left_x"], "right_x": pair["right_x"],
                    "left_value": pair["left_value"], "right_value": pair["right_value"],
                    "width": pair["width"], "velocity_l": vel_l, "velocity_r": vel_r,
                    "last_y": y, "gap": 0, "score": score, "node": node,
                })
            if transitions:
                transitions.sort(key=lambda item: item["score"], reverse=True)
                next_beam.extend(transitions[:12])
                continue

            missing = _missing_row(evidence, y, state["left_x"], state["right_x"])
            # Only an evidence-obscured short patch may be bridged.  A valid
            # row whose coupled pair cannot be matched ends this walk instead
            # of silently skipping a confrontation with a neighbouring groove.
            if missing["reason"] == "dark_spot" and state["gap"] < MAX_GAP_ROWS:
                node = {"row": missing, "parent": state["node"]}
                next_beam.append({
                    **state,
                    "gap": state["gap"] + 1,
                    "score": state["score"] - 2.5,
                    "node": node,
                })
            else:
                rows = _reconstruct(state["node"])
                if len(rows) >= 1:
                    finished.append((state["score"], rows))

        # Deduplicate near-identical states before beam pruning.
        unique = {}
        for state in next_beam:
            key = (
                round(state["left_x"] / 2), round(state["right_x"] / 2),
                state["last_y"], state["gap"],
            )
            old = unique.get(key)
            if old is None or state["score"] > old["score"]:
                unique[key] = state
        beam = sorted(unique.values(), key=lambda item: item["score"], reverse=True)[:BEAM_WIDTH]
        if not beam:
            break

    for state in beam:
        rows = _reconstruct(state["node"])
        if rows:
            finished.append((state["score"], rows))
    if not finished:
        return [seed_row]

    def path_rank(item):
        _, rows = item
        measured = [row for row in rows if row["measured"]]
        if not measured:
            return (-1e9, 0, 0)
        weak = min(row["weak_contrast"] for row in measured)
        # Prefer more confirmed rows, with a secondary contrast tie-break.
        return (len(measured), weak, item[0])

    finished.sort(key=path_rank, reverse=True)
    return finished[0][1]


def _summary(rows):
    measured = [row for row in rows if row["measured"]]
    gaps = [row for row in rows if not row["measured"]]
    widths = [row["width"] for row in measured]
    contrasts = [row["weak_contrast"] for row in measured]
    ys = [row["y"] for row in rows]
    return {
        "start_y": min(ys), "end_y": max(ys), "span_rows": max(ys) - min(ys) + 1,
        "measured_rows": len(measured), "gap_rows": len(gaps),
        "widths_measured": len(widths),
        "min_width": min(widths) if widths else None,
        "max_width": max(widths) if widths else None,
        "mean_width": _round(sum(widths) / len(widths), 2) if widths else None,
        "min_contrast": min(contrasts) if contrasts else None,
        "median_contrast": _round(float(np.median(contrasts)), 2) if contrasts else None,
    }


def _prefix_rows(rows, end_y):
    return [deepcopy(row) for row in rows if row["y"] <= end_y]


def _recommend(rows, threshold):
    measured = [row for row in rows if row["measured"]]
    if not measured:
        raise Invalid("追踪结果中没有可量宽度的原行")
    clear_floor = max(float(threshold), 10.0)
    hard_floor = max(5.0, float(threshold) * 0.45)

    # Stop before the first sustained run of unclear walls; one transient dip
    # does not discard an otherwise clear continuation.
    clear_end = measured[0]["y"]
    weak_run = 0
    for row in measured:
        if row["weak_contrast"] + 1e-9 >= clear_floor:
            clear_end = row["y"]
            weak_run = 0
        else:
            weak_run += 1
            if weak_run >= 3:
                break
    clear_rows = _prefix_rows(rows, clear_end)
    clear_summary = _summary(clear_rows)

    # A hard floor gives the longest still-recognisable continuation.
    hard_end = measured[0]["y"]
    weak_run = 0
    for row in measured:
        if row["weak_contrast"] + 1e-9 >= hard_floor:
            hard_end = row["y"]
            weak_run = 0
        else:
            weak_run += 1
            if weak_run >= 3:
                break
    long_rows = _prefix_rows(rows, hard_end)
    long_summary = _summary(long_rows)
    full_summary = _summary(rows)

    extra_rows = long_summary["measured_rows"] - clear_summary["measured_rows"]
    contrast_cost = None
    if clear_summary["min_contrast"] is not None and long_summary["min_contrast"] is not None:
        contrast_cost = _round(max(0.0, clear_summary["min_contrast"] - long_summary["min_contrast"]), 2)
    worthwhile = (
        extra_rows >= 5
        and long_summary["min_contrast"] is not None
        and long_summary["min_contrast"] >= hard_floor
        and (
            clear_summary["min_contrast"] is None
            or long_summary["min_contrast"] >= 0.55 * clear_summary["min_contrast"]
        )
    )
    chosen_id = "balanced_long" if worthwhile else "clearer"
    chosen_rows = long_rows if worthwhile else clear_rows
    chosen_summary = _summary(chosen_rows)

    if extra_rows < 3:
        message = (
            f"建议采用 Y {chosen_summary['start_y']}–{chosen_summary['end_y']}："
            f"可量 {chosen_summary['measured_rows']} 行"
            + (f"、{chosen_summary['gap_rows']} 行暗斑留空" if chosen_summary["gap_rows"] else "")
            + f"，较弱一坡最低反差 {chosen_summary['min_contrast']}%；"
            "没有需要以明显降反差换取的大段延伸。"
        )
    elif worthwhile:
        message = (
            f"建议多量到 Y {long_summary['end_y']}（共 {long_summary['measured_rows']} 行，"
            f"范围 Y {long_summary['start_y']}–{long_summary['end_y']}）。"
            f"相比收在 Y {clear_end} 的清楚走法，可多量 {extra_rows} 行；"
            f"弱侧最低反差由 {clear_summary['min_contrast']}% 降到 "
            f"{long_summary['min_contrast']}%，反差代价约 {contrast_cost} 个百分点。"
            "两坡仍成对连续，邻沟没有造成单边跳接。"
        )
    else:
        message = (
            f"建议收在 Y {clear_end}，采用 Y {clear_summary['start_y']}–{clear_summary['end_y']}："
            f"可量 {clear_summary['measured_rows']} 行，弱侧最低反差 "
            f"{clear_summary['min_contrast']}%。"
            f"继续到 Y {hard_end} 虽可多量 {extra_rows} 行，但弱侧最低会降到 "
            f"{long_summary['min_contrast']}%（反差代价约 {contrast_cost} 个百分点），"
            "且更容易被邻沟或暗斑带跳，优先保住两边清楚。"
        )

    alternatives = [
        {"id": "recommended", "label": "建议范围", "summary": chosen_summary, "rows": chosen_rows},
        {"id": "clearer", "label": "更清楚但较短", "summary": clear_summary, "rows": clear_rows},
        {"id": "longest", "label": "最长可认范围", "summary": long_summary, "rows": long_rows},
        {"id": "all_detected", "label": "全部检测到的走法", "summary": full_summary, "rows": rows},
    ]
    return {
        "id": "recommended",
        "policy": chosen_id,
        "choice": "recommended",
        "message": message,
        "contrast_cost_points": contrast_cost,
        "clear_end_y": clear_end,
        "long_end_y": hard_end,
        "summary": chosen_summary,
        "alternatives": alternatives,
    }


def trace_groove(evidence, settings, x, y=None, direction="down", choice="recommended"):
    """Build a proposal containing the coupled row-by-row groove track."""
    x = int(x)
    if y is None:
        y = int(settings["scan_y"])
    y = int(y)
    bounds = evidence.bounds
    if not bounds.area or not bounds.contains(x, y):
        raise Invalid("请在当前量区和共同覆盖内的横切线上点选一条沟")
    track_both = direction in ("both", "vertical")
    if direction in ("up", "-1", -1):
        dir_value = -1
        direction_name = "up"
    elif track_both:
        dir_value = 1
        direction_name = "both"
    else:
        dir_value = 1
        direction_name = "down"
    seed = _seed_pair(evidence, x, y)
    if track_both:
        upward = list(reversed(_track_direction(evidence, settings, seed, -1)))
        downward = _track_direction(evidence, settings, seed, 1)
        rows = upward[:-1] + downward
    else:
        rows = _track_direction(evidence, settings, seed, dir_value)
    recommendation = _recommend(rows, settings.get("threshold", 12.0))
    choice_id = choice if choice in {"recommended", "clearer", "longest", "all_detected"} else "recommended"
    chosen = next(item for item in recommendation["alternatives"] if item["id"] == choice_id)
    if choice_id == "recommended":
        chosen = next(item for item in recommendation["alternatives"] if item["id"] == recommendation["id"])
    adoption_rows = deepcopy(chosen["rows"])
    adoption = {
        "schema": SCHEMA,
        "kind": "groove_width",
        "choice": choice_id,
        "seed": {"x": x, "y": y},
        "direction": direction_name,
        "start_y": chosen["summary"]["start_y"],
        "end_y": chosen["summary"]["end_y"],
        "rows": adoption_rows,
        "summary": chosen["summary"],
        "recommendation": {
            "id": recommendation["id"], "message": recommendation["message"],
            "contrast_cost_points": recommendation["contrast_cost_points"],
            "clear_end_y": recommendation["clear_end_y"], "long_end_y": recommendation["long_end_y"],
            "selected_label": chosen["label"],
        },
    }
    return {
        "schema": SCHEMA,
        "kind": "groove_width_proposal",
        "seed": {"x": x, "y": y},
        "direction": direction_name,
        "seed_pair": {k: v for k, v in seed.items() if not k.endswith("_peak")},
        "rows": rows,
        "summary": _summary(rows),
        "recommendation": recommendation,
        "adoption": adoption,
        # Common aliases used by different parts of the desk/API vocabulary.
        "track": adoption,
        "groove": adoption,
        "proposal": adoption,
    }


def groove_at(groove, y):
    """Return the adopted row at an original left-image Y, if present."""
    if not isinstance(groove, dict):
        return None
    for row in groove.get("rows", []):
        if isinstance(row, dict) and row.get("y") == y:
            return row
    return None


def validate_groove(raw, left_size):
    """Validate an adopted track stored in card metadata."""
    raw = object_value(raw, "浅沟追踪结果")
    width, height = left_size
    rows_raw = raw.get("rows")
    if not isinstance(rows_raw, list) or not rows_raw:
        raise Invalid("采用的浅沟至少要有一行两坡峰距")
    if len(rows_raw) > height + 1:
        raise Invalid("浅沟追踪行数过多")
    rows = []
    last_y = None
    measured = 0
    for item in rows_raw:
        item = object_value(item, "浅沟原行")
        y = integer(item.get("y"), "浅沟行号", 0, height - 1)
        if last_y is not None and y <= last_y:
            raise Invalid("浅沟行号必须严格递增")
        last_y = y
        row = {"y": y}
        width_value = item.get("width")
        if width_value is None:
            row.update({
                "measured": False, "gap": True, "width": None,
                "left_x": None, "right_x": None, "left_contrast": None,
                "right_contrast": None, "weak_contrast": None,
                "left_source": None, "right_source": None,
                "reason": text(item.get("reason", "dark_spot"), "缺证原因", 40),
            })
        else:
            groove_width = integer(width_value, "两坡峰距", 1, width)
            lx = integer(item.get("left_x"), "左坡峰列", 0, width - 1)
            rx = integer(item.get("right_x"), "右坡峰列", 0, width - 1)
            if rx - lx != groove_width:
                raise Invalid("两坡峰距必须等于同一原行右峰列减左峰列")
            measured += 1
            row.update({
                "measured": True, "gap": False, "width": groove_width,
                "left_x": lx, "right_x": rx,
                "left_contrast": _optional_number(item.get("left_contrast"), "左坡反差"),
                "right_contrast": _optional_number(item.get("right_contrast"), "右坡反差"),
                "weak_contrast": _optional_number(item.get("weak_contrast"), "弱侧反差"),
                "left_source": _validate_source(item.get("left_source"), lx, y),
                "right_source": _validate_source(item.get("right_source"), rx, y),
            })
        rows.append(row)
    if not measured:
        raise Invalid("采用的浅沟至少要有一行可量宽度")
    seed = raw.get("seed") if isinstance(raw.get("seed"), dict) else {"x": rows[0].get("left_x") or 0, "y": rows[0]["y"]}
    clean_seed = {
        "x": integer(seed.get("x", 0), "点选列", 0, width - 1),
        "y": integer(seed.get("y", rows[0]["y"]), "点选行", 0, height - 1),
    }
    recommendation = raw.get("recommendation") if isinstance(raw.get("recommendation"), dict) else {}
    return {
        "schema": text(raw.get("schema", SCHEMA), "结构版本", 80),
        "kind": "groove_width",
        "seed": clean_seed,
        "direction": raw.get("direction") if raw.get("direction") in ("down", "up", "both") else "down",
        "start_y": rows[0]["y"],
        "end_y": rows[-1]["y"],
        "rows": rows,
        "summary": _summary(rows),
        "recommendation": {
            "message": text(recommendation.get("message", "已采用这道沟的成对坡峰追踪。"), "建议说明", 500),
            "contrast_cost_points": recommendation.get("contrast_cost_points"),
            "id": text(recommendation.get("id", "adopted"), "建议编号", 80),
        },
    }


def _optional_number(value, label):
    if value is None:
        return None
    from image_lab.validation import number
    return number(value, label, -100, 100)


def _validate_source(raw, x, y):
    if not isinstance(raw, dict):
        return None
    return {
        "side": raw.get("side") if raw.get("side") in ("left", "right") else "source",
        "x": int(x), "y": int(y),
        "right_x": raw.get("right_x"), "right_y": raw.get("right_y"),
        "response": raw.get("response"), "valid": True,
    }
