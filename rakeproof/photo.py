"""Normalize upload orientation without changing the source pixel grid thereafter."""

from io import BytesIO

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid


MAX_IMAGE_BYTES = 14 * 1024 * 1024
MAX_PIXELS = 2_600_000
FORMATS = {"PNG", "JPEG", "TIFF", "BMP", "WEBP"}


def encode(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def decode(content):
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise Invalid("每张原照须小于 14 MiB")
    try:
        with Image.open(BytesIO(content)) as photo:
            if photo.format not in FORMATS or getattr(photo, "n_frames", 1) != 1:
                raise Invalid("请上传单帧 PNG、JPEG、TIFF、BMP 或 WebP")
            width, height = photo.size
            if min(width, height) < 24 or max(width, height) > 2300 or width * height > MAX_PIXELS:
                raise Invalid("照片边长须为 24–2300 像素，总像素不得超过 260 万")
            oriented = ImageOps.exif_transpose(photo)
            if "A" in oriented.getbands() or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                if rgba.getchannel("A").getextrema() != (255, 255):
                    raise Invalid("斜照原片须为完全不透明的照片")
            rgb = oriented.convert("RGB")
            return rgb.size, encode(rgb)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise Invalid("无法解码这张照片") from error


def pixels(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def luminance(rgb):
    return rgb.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
