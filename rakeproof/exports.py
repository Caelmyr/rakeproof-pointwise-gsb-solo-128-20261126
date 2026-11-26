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


def channel_section_csv(report):
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow((
        "left_x", "left_y", "right_x", "right_y", "caliber", "channels",
        "channel_pct", "original_pct", "original_valid", "R_left", "R_right",
        "G_left", "G_right", "B_left", "B_right",
    ))
    for sample in report.get("samples", []):
        caliber = sample.get("recommended_caliber") or ""
        channels = "" if not sample.get("recommended_channels") else "+".join(sample["recommended_channels"])
        writer.writerow((
            sample["x"], report.get("y"), sample["right_x"], sample["right_y"],
            caliber, channels, sample.get("recommended_value"),
            sample.get("original_value"), sample.get("original_valid"),
            sample["channels"]["R"]["left_value"], sample["channels"]["R"]["right_value"],
            sample["channels"]["G"]["left_value"], sample["channels"]["G"]["right_value"],
            sample["channels"]["B"]["left_value"], sample["channels"]["B"]["right_value"],
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
    if 0 <= marker < box.height:
        ImageDraw.Draw(base).line((0, marker, box.width - 1, marker), width=1, fill=(241, 232, 176, 255))
    return encode(base)


def bundle(card, left, right, evidence, report):
    output = BytesIO()
    with ZipFile(output, mode="w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("left-source.png", encode(Image.fromarray(left)))
        zf.writestr("right-source.png", encode(Image.fromarray(right)))
        zf.writestr("zone-proof.png", proof(card, left, evidence))
        zf.writestr("section.csv", section_csv(report))
        if report.get("channel_section"):
            zf.writestr("channel-section.csv", channel_section_csv(report["channel_section"]))
        metadata = {
            "card": card, "calibration": report["calibration"], "counts": report["counts"],
            "coordinates": "left normalized source; boxes are half-open; right = left - adopted shift",
        }
        zf.writestr("inspection.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    return output.getvalue()
