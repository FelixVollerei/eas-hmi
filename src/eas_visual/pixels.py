"""Integer geometry and metrics. Only selected pixels become edit layers."""

import hashlib
from io import BytesIO
from itertools import pairwise
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .errors import require

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)


def png(image):
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def read_image(data, opaque=True):
    with Image.open(BytesIO(data)) as source:
        require(source.width * source.height <= 4_000_000, "IMAGE_TOO_LARGE", "v0.1 supports up to 4M pixels")
        rgba = source.convert("RGBA")
        if opaque:
            require(
                rgba.getchannel("A").getextrema() == (255, 255), "ALPHA_UNSUPPORTED", "Opaque patch required"
            )
        return rgba.convert("RGB") if opaque else rgba


def pixel_hash(image):
    rgb = image.convert("RGB")
    return hashlib.sha256(f"{rgb.width}x{rgb.height}:RGB:".encode() + rgb.tobytes()).hexdigest()


def mask_png(mask):
    return png(Image.fromarray(mask.astype(np.uint8) * 255))


def read_mask(data, size):
    with Image.open(BytesIO(data)) as image:
        require(image.size == tuple(size), "MASK_SIZE", "Mask must match full scene dimensions")
        require(image.mode in ("1", "L"), "INVALID_MASK", "Use a binary grayscale mask PNG")
        values = np.asarray(image.convert("L"))
    require(np.isin(values, [0, 255]).all(), "INVALID_MASK", "Mask must contain only 0 and 255")
    mask = values == 255
    require(mask.any(), "EMPTY_MASK", "Mask is empty")
    return mask


def box(mask):
    yy, xx = np.where(mask)
    require(len(xx) > 0, "EMPTY_MASK", "Mask is empty")
    return [int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1]


def validate_box(value, size):
    require(
        isinstance(value, (list, tuple)) and len(value) == 4 and all(type(v) is int for v in value),
        "INVALID_BBOX",
        "bbox requires four integer coordinates [x0,y0,x1,y1)",
    )
    x0, y0, x1, y1 = value
    require(0 <= x0 < x1 <= size[0] and 0 <= y0 < y1 <= size[1], "INVALID_BBOX", "bbox outside canvas")
    return list(value)


def selection(size, bbox=None, polygon=None, mask_data=None):
    require(
        sum(v is not None for v in (bbox, polygon, mask_data)) == 1,
        "INVALID_SELECTION",
        "Provide exactly one bbox, polygon or mask",
    )
    if mask_data is not None:
        return read_mask(mask_data, size)
    drawing = Image.new("L", size)
    if bbox is not None:
        x0, y0, x1, y1 = validate_box(bbox, size)
        ImageDraw.Draw(drawing).rectangle((x0, y0, x1 - 1, y1 - 1), fill=255)
    else:
        require(
            isinstance(polygon, list) and len(polygon) >= 3,
            "INVALID_POLYGON",
            "At least three points required",
        )
        require(
            all(isinstance(p, list) and len(p) == 2 and all(type(v) is int for v in p) for p in polygon),
            "INVALID_POLYGON",
            "Integer coordinate pairs required",
        )
        require(
            all(0 <= x < size[0] and 0 <= y < size[1] for x, y in polygon),
            "INVALID_POLYGON",
            "Polygon outside canvas; no clipping",
        )
        ImageDraw.Draw(drawing).polygon([tuple(p) for p in polygon], fill=255)
    return np.asarray(drawing) > 0


def dilate(mask, margin):
    require(type(margin) is int and 0 <= margin <= 8, "INVALID_MARGIN", "margin must be 0..8 integer pixels")
    if not margin:
        return mask.copy()
    return (
        np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(margin * 2 + 1)))
        > 0
    )


def translated(mask, dx, dy):
    require(
        type(dx) is int and type(dy) is int and (dx != 0 or dy != 0),
        "INVALID_TRANSFORM",
        "Use a nonzero integer translation",
    )
    yy, xx = np.where(mask)
    require(
        (xx + dx).min() >= 0
        and (yy + dy).min() >= 0
        and (xx + dx).max() < mask.shape[1]
        and (yy + dy).max() < mask.shape[0],
        "TARGET_OUTSIDE",
        "Target would clip outside canvas",
    )
    result = np.zeros_like(mask)
    result[yy + dy, xx + dx] = True
    return result


def metrics(before, after, support):
    require(before.size == after.size, "RENDER_SIZE", "Renderer dimensions changed")
    delta = np.abs(np.asarray(before, dtype=np.int16) - np.asarray(after, dtype=np.int16))
    untouched = delta[~support]
    require(untouched.size > 0, "INVALID_SUPPORT", "Whole-image editing is not supported")
    changed = np.any(untouched != 0, axis=1)
    return {
        "untouched_region_mae": float(untouched.mean()),
        "untouched_changed_pixels": int(changed.sum()),
        "untouched_changed_pixel_ratio": float(changed.mean()),
        "untouched_max_absolute_difference": int(untouched.max()),
        "editable_mask_pixels": int(support.sum()),
        "editable_mask_ratio": float(support.mean()),
        "output_hash": pixel_hash(after),
    }


def vectorize(image, mask):
    """Return standalone local SVG and placement bbox; never embeds an image."""
    x0, y0, x1, y1 = box(mask)
    a = np.asarray(image, dtype=np.int64)[y0:y1, x0:x1]
    values = (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]
    values[~mask[y0:y1, x0:x1]] = -1
    active, completed = {}, {}

    def finish(key, span):
        left, right, color = key
        top, bottom = span
        completed.setdefault(color, []).append(f"M{left},{top}h{right - left}v{bottom - top}h{left - right}z")

    for y, row in enumerate(values):
        splits = np.r_[0, np.flatnonzero(row[1:] != row[:-1]) + 1, len(row)]
        current = {}
        for left, right in pairwise(splits):
            color = int(row[left])
            if color < 0:
                continue
            key = int(left), int(right), color
            old = active.pop(key, None)
            current[key] = (old[0] if old else y, y + 1)
        for key, span in active.items():
            finish(key, span)
        active = current
    for key, span in active.items():
        finish(key, span)
    root = ET.Element(
        f"{{{SVG}}}svg",
        width=str(x1 - x0),
        height=str(y1 - y0),
        viewBox=f"0 0 {x1 - x0} {y1 - y0}",
        attrib={"shape-rendering": "crispEdges"},
    )
    for color, paths in sorted(completed.items()):
        ET.SubElement(root, f"{{{SVG}}}path", fill=f"#{color:06x}", d="".join(paths))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), [x0, y0, x1, y1]
