"""Stage 2 asset backend: Inkscape CLI + Pillow, never GUI automation."""

import math
import os
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from lxml import etree
from PIL import Image, ImageColor

from ..errors import EngineeringError
from ..operations.transaction import atomic_write
from ..svg.common import number, safe_parse, tag, xml_id
from ..svg.renderer import render_tree


class InkscapeBackend:
    def __init__(self, executable=None, timeout=30):
        candidates = (
            [executable]
            if executable
            else [
                os.environ.get("EAS_HMI_INKSCAPE"),
                shutil.which("inkscape"),
                r"C:\Program Files\Inkscape\bin\inkscape.com",
            ]
        )
        self.executable = next((str(Path(p)) for p in candidates if p and Path(p).is_file()), None)
        if not self.executable:
            raise EngineeringError(
                "RASTERIZER_UNAVAILABLE", "Install Inkscape or set EAS_HMI_INKSCAPE to its executable"
            )
        self.timeout = timeout

    def run(self, args):
        try:
            result = subprocess.run(
                [self.executable, *map(str, args)],
                capture_output=True,
                check=False,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EngineeringError("RASTERIZER_FAILED", f"Inkscape execution failed: {exc}") from exc
        if result.returncode:
            raise EngineeringError("RASTERIZER_FAILED", result.stderr or result.stdout)
        return result

    def version(self):
        return self.run(["--version"]).stdout.strip()

    def drawing_bounds(self, data, object_id):
        safe_parse(data)
        with tempfile.TemporaryDirectory(prefix="eas-hmi-bounds-") as folder:
            path = Path(folder) / "input.svg"
            path.write_bytes(data)
            result = self.run([path, "--query-all"])
        for line in result.stdout.splitlines():
            fields = line.split(",")
            if len(fields) == 5 and fields[0] == object_id:
                try:
                    x, y, w, h = map(float, fields[1:])
                except ValueError as exc:
                    raise EngineeringError(
                        "INVALID_ASSET_BOUNDS", "Backend returned nonnumeric bounds"
                    ) from exc
                if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
                    raise EngineeringError(
                        "INVALID_ASSET_BOUNDS", "Cannot export empty/nonfinite drawing bounds"
                    )
                return x, y, w, h
        raise EngineeringError(
            "INVALID_ASSET_BOUNDS", f"Backend did not return drawing bounds for {object_id}"
        )

    def png(self, data, width, height):
        if (
            not isinstance(width, int)
            or not isinstance(height, int)
            or not 1 <= width <= 16384
            or not 1 <= height <= 16384
        ):
            raise EngineeringError("INVALID_ASSET_SIZE", "Dimensions must be integer pixels in 1..16384")
        safe_parse(data)
        with tempfile.TemporaryDirectory(prefix="eas-hmi-export-") as folder:
            source, output = Path(folder) / "input.svg", Path(folder) / "output.png"
            source.write_bytes(data)
            result = self.run(
                [
                    source,
                    "--export-type=png",
                    "--export-area-page",
                    f"--export-width={width}",
                    f"--export-height={height}",
                    "--export-background-opacity=0",
                    "--export-png-color-mode=RGBA_8",
                    f"--export-filename={output}",
                ]
            )
            try:
                image = Image.open(output)
                image.load()
                if image.size != (width, height) or image.format != "PNG":
                    raise EngineeringError(
                        "INVALID_RASTER_OUTPUT", f"Unexpected raster size/format: {image.size}/{image.format}"
                    )
                return image.convert("RGBA"), result.stderr.strip()
            except (OSError, ValueError) as exc:
                raise EngineeringError(
                    "INVALID_RASTER_OUTPUT", "Inkscape did not produce a decodable PNG"
                ) from exc


def asset_svg(project, page_id, node_id=None, backend=None):
    root = render_tree(project, page_id, background=node_id is None)
    root.set("preserveAspectRatio", "none")
    # Inkscape 1.4.2 computes a parent group's bounds incorrectly around nested
    # svg x/y. Flatten only our known, overflow-visible frames in the derived
    # asset, using the exactly equivalent translate/scale transform.
    for frame in root.xpath('//*[@data-eas-part="frame"]'):
        _, _, cw, ch = map(float, frame.get("viewBox").split())
        x, y, w, h = (float(frame.get(key)) for key in ("x", "y", "width", "height"))
        frame.tag = tag("g")
        for key in ("x", "y", "width", "height", "viewBox", "preserveAspectRatio", "overflow", "version"):
            frame.attrib.pop(key, None)
        frame.set("transform", f"translate({number(x)} {number(y)}) scale({number(w / cw)} {number(h / ch)})")
    root.set("data-eas-artifact", "asset")
    if node_id:
        matches = root.xpath("//*[@data-eas-id=$id]", id=node_id)
        if not matches:
            raise EngineeringError("OBJECT_NOT_FOUND", f"Node not on page: {node_id}")
        target = matches[0]
        # Retain the ancestor transforms/visibility, remove sibling artwork.
        branch = target
        while branch.getparent() is not None:
            parent = branch.getparent()
            for child in list(parent):
                if child is not branch:
                    parent.remove(child)
            branch = parent
        query_bytes = etree.tostring(root, encoding="utf-8")
        x, y, w, h = (backend or InkscapeBackend()).drawing_bounds(query_bytes, xml_id(node_id))
        root.set("viewBox", " ".join(number(v) for v in (x, y, w, h)))
        root.set("width", number(w))
        root.set("height", number(h))
    return etree.tostring(root, encoding="utf-8", xml_declaration=True)


def export_asset(
    project, page_id, destination, node_id=None, width=None, height=None, background="#ffffff", backend=None
):
    path = Path(destination)
    suffix = path.suffix.lower()
    if suffix not in (".svg", ".png", ".bmp"):
        raise EngineeringError("INVALID_ASSET_FORMAT", "Stage 2 exports SVG, PNG or BMP")
    if (width is None) != (height is None):
        raise EngineeringError("INVALID_ASSET_SIZE", "Specify both dimensions, or neither")
    backend = backend or (InkscapeBackend() if node_id or suffix != ".svg" else None)
    data = asset_svg(project, page_id, node_id, backend)
    root = safe_parse(data)
    if width is None:
        width, height = (
            max(1, math.ceil(float(root.get("width")))),
            max(1, math.ceil(float(root.get("height")))),
        )
    if (
        not isinstance(width, int)
        or not isinstance(height, int)
        or not 1 <= width <= 16384
        or not 1 <= height <= 16384
    ):
        raise EngineeringError("INVALID_ASSET_SIZE", "Dimensions must be integer pixels in 1..16384")
    root.set("width", str(width))
    root.set("height", str(height))
    data = etree.tostring(root, encoding="utf-8", xml_declaration=True)
    warnings = []
    if suffix != ".svg":
        image, stderr = backend.png(data, width, height)
        if stderr:
            warnings.append(stderr)
        if suffix == ".bmp":
            try:
                rgb = ImageColor.getrgb(background)
                if len(rgb) != 3:
                    raise ValueError("BMP background must be opaque RGB")
            except ValueError as exc:
                raise EngineeringError("INVALID_BACKGROUND", str(exc)) from exc
            canvas = Image.new("RGBA", image.size, (*rgb, 255))
            image = Image.alpha_composite(canvas, image).convert("RGB")
        stream = BytesIO()
        image.save(stream, format="PNG" if suffix == ".png" else "BMP")
        data = stream.getvalue()
    atomic_write(path, data)
    return {
        "path": str(path.resolve()),
        "format": suffix[1:],
        "width": width,
        "height": height,
        "background": background if suffix == ".bmp" else None,
        "warnings": warnings,
        "viewBox": root.get("viewBox"),
    }
