"""Download a reproducible section, cropped proof, or the inspection evidence together."""

import csv
import json
from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageDraw

from .analysis import map_image
from .geometry import read_box
from .photo import encode


def section_csv(summary):
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("left_x", "left_y", "right_x", "right_y", "left_gray", "right_gray", "signed_pct"))
    section = summary["section"]
    writer.writerows(
        (sample["x"], section["y"], sample["right_x"], sample["right_y"],
         sample["left_gray"], sample["right_gray"], sample["value"])
        for sample in section["samples"]
    )
    return output.getvalue().encode("utf-8")


def groove_csv(summary):
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow((
        "left_y", "width_px", "left_wall_x", "right_wall_x",
        "left_wall_response_pct", "right_wall_response_pct", "weak_wall_response_pct",
        "status", "left_source_right_x", "left_source_right_y",
        "right_source_right_x", "right_source_right_y",
    ))
    groove = summary.get("groove") or summary.get("track") or {}
    for row in groove.get("rows", []):
        left_source = row.get("left_source") or {}
        right_source = row.get("right_source") or {}
        if row.get("measured"):
            status = "measured"
        elif row.get("reason") == "dark_spot":
            status = "blank_dark_or_exposure"
        else:
            status = "blank_unmatched"
        writer.writerow((
            row.get("y"), "" if row.get("width") is None else row.get("width"),
            "" if row.get("left_x") is None else row.get("left_x"),
            "" if row.get("right_x") is None else row.get("right_x"),
            "" if row.get("left_contrast") is None else row.get("left_contrast"),
            "" if row.get("right_contrast") is None else row.get("right_contrast"),
            "" if row.get("weak_contrast") is None else row.get("weak_contrast"),
            status,
            left_source.get("right_x", ""), left_source.get("right_y", ""),
            right_source.get("right_x", ""), right_source.get("right_y", ""),
        ))
    return output.getvalue().encode("utf-8")


def proof(card, left, evidence):
    s = card["settings"]
    box = read_box(s["zone"], card["left_size"], "量测框")
    limit = (box.left, box.top, box.right, box.bottom)
    base = Image.fromarray(left).convert("RGBA").crop(limit)
    overlay = map_image(card["left_size"], evidence, s["threshold"]).crop(limit)
    base = Image.alpha_composite(base, overlay)
    marker = s["scan_y"] - box.top
    draw = ImageDraw.Draw(base)
    if 0 <= marker < box.height:
        draw.line((0, marker, box.width - 1, marker), width=1, fill=(241, 232, 176, 255))
    groove = card.get("groove")
    if groove and groove.get("rows"):
        colors = {"left_x": (115, 181, 228, 230), "right_x": (240, 164, 116, 230)}
        for key, color in colors.items():
            points = []
            for row in groove["rows"]:
                if row.get("measured") and box.contains(row[key], row["y"]):
                    points.append((row[key] - box.left, row["y"] - box.top))
                elif len(points) >= 2:
                    draw.line(points, fill=color, width=2)
                    points = []
            if len(points) >= 2:
                draw.line(points, fill=color, width=2)
        active = next((row for row in groove["rows"] if row.get("y") == s["scan_y"] and row.get("measured")), None)
        if active:
            y = active["y"] - box.top
            x1 = active["left_x"] - box.left
            x2 = active["right_x"] - box.left
            draw.line((x1, y, x2, y), fill=(229, 213, 148, 255), width=2)
            for x, color in ((x1, (115, 181, 228, 255)), (x2, (240, 164, 116, 255))):
                draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color, outline=(255, 255, 255, 255))
    return encode(base)


def bundle(card, left, right, evidence, report):
    output = BytesIO()
    with ZipFile(output, mode="w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("left-source.png", encode(Image.fromarray(left)))
        zf.writestr("right-source.png", encode(Image.fromarray(right)))
        zf.writestr("zone-proof.png", proof(card, left, evidence))
        zf.writestr("section.csv", section_csv(report))
        metadata = {
            "card": card, "calibration": report["calibration"], "counts": report["counts"],
            "coordinates": "left normalized source; boxes are half-open; right = left - adopted shift",
        }
        zf.writestr("inspection.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    return output.getvalue()
