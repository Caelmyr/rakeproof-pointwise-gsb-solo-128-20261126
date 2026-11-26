"""Deterministic synthetic labeled examples of two opposite illuminations."""

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

from .geometry import Box, default_settings
from .photo import encode


SIZE = (760, 468)
EXERCISES = {
    "tooling": "刻线与印痕",
    "ink": "薄印字与浅沟",
    "shadow": "斜照亮斑和遮挡",
}


def substrate(x, y, style, rng):
    texture = gaussian_filter(rng.normal(0, 1, x.shape), 0.65) * 5
    base = 156 + 7 * np.sin(x / 44) + 3 * np.cos(y / 33) + texture
    if style == "ink":
        letters = ((x > 346) & (x < 414) & (y > 113) & (y < 341)) | ((x > 288) & (x < 497) & (y > 129) & (y < 173))
        base -= 57 * letters
    return base


def panel(x, y, style):
    length = np.clip(np.minimum((y - 91) / 22, (410 - y) / 26), 0, 1)
    middle = 305 + 12 * np.sin((y - 135) / 120)
    groove = -21 * np.exp(-((x - middle) / 17) ** 4) * length
    faint = -10 * np.exp(-((x - (middle + 43 + 0.05 * (y - 210))) / 13) ** 4) * length
    ridge = 12 * np.exp(-((x - 483 + 0.11 * (y - 230)) / 22) ** 4) * length
    tooth = -13 * np.exp(-((x - 580) / 38) ** 2 - ((y - 256) / 103) ** 2)
    if style == "ink":
        return groove * 0.75 + faint * 0.65 + ridge
    return groove + faint + ridge + tooth


def picture(style, direction, phase):
    w, h = SIZE
    y, x = np.mgrid[:h, :w].astype(np.float32)
    x -= phase[0]
    y -= phase[1]
    base = substrate(x, y, style, np.random.default_rng(814 + direction))
    height = panel(x, y, style)
    slope = np.gradient(height, axis=1)
    gray = (base + 39 * slope * direction) * (0.90 if direction == -1 else 1.03)
    if style == "shadow":
        if direction == -1:
            eclipsed = ((x - 330) / 141) ** 2 + ((y - 262) / 28) ** 2 < 1
            short_shadow = ((x - 309) / 46) ** 2 + ((y - 205) / 3.5) ** 2 < 1
            eclipsed |= short_shadow
            gray[eclipsed] = 5 + rng_values(eclipsed.sum())
        else:
            gray += 133 * np.exp(-((x - 518) / 51) ** 2 - ((y - 203) / 46) ** 2)
    palette = np.stack((gray * 1.05, gray * 0.96 + 7, gray * 0.83 + 8), axis=-1)
    array = np.clip(np.rint(palette), 0, 255).astype(np.uint8)
    return SIZE, encode(Image.fromarray(array))


def rng_values(count):
    return np.random.default_rng(217).normal(0, 0.6, count)


def make(style):
    phase = (9, -6) if style == "shadow" else (5, 3)
    left = (f"{style}-left.png", *picture(style, 1, (0, 0)))
    right = (f"{style}-right.png", *picture(style, -1, phase))
    settings = default_settings(SIZE)
    settings.update({
        "dx": -phase[0], "dy": -phase[1], "zone": Box(165, 98, 616, 393).record(),
        "flat": Box(62, 54, 147, 128).record(), "balance": True,
        "scan_y": 256 if style == "shadow" else 232, "threshold": 11.0,
    })
    return EXERCISES[style], left, right, settings
