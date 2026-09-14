"""Raster-only reconstruction probe: quantize -> trace -> EAS -> render -> compare.

The input must be a bitmap. Never reads a corresponding source SVG/model.
This tests visual fidelity, not semantic decomposition or editability.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import vtracer
from PIL import Image

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def edges(a):
    a = a.astype(np.int16)
    out = np.zeros(a.shape[:2], dtype=bool)
    out[:, 1:] |= np.max(np.abs(a[:, 1:] - a[:, :-1]), axis=2) > 24
    out[1:, :] |= np.max(np.abs(a[1:, :] - a[:-1, :]), axis=2) > 24
    return out


def dilate(a):
    p = np.pad(a, 1)
    h, w = a.shape
    return np.logical_or.reduce([p[y:y+h, x:x+w] for y in range(3) for x in range(3)])


def metrics(reference, actual):
    a, b = np.array(reference.convert("RGB")), np.array(actual.convert("RGB"))
    assert a.shape == b.shape
    error = np.abs(a.astype(np.int16) - b.astype(np.int16))
    pixel = error.max(axis=2)
    ea, eb = edges(a), edges(b)
    recall = float((ea & dilate(eb)).sum() / max(1, ea.sum()))
    precision = float((eb & dilate(ea)).sum() / max(1, eb.sum()))
    return {
        "rgb_mae_0_255": float(error.mean()),
        "pixel_max_channel_error_p99": float(np.percentile(pixel, 99)),
        "pixels_max_channel_error_le_8_percent": float((pixel <= 8).mean() * 100),
        "edge_precision_1px": precision, "edge_recall_1px": recall,
        "edge_f1_1px": 2 * precision * recall / max(1e-12, precision + recall),
    }


def inspect_svg(path):
    tree = ET.parse(path)
    tags = [e.tag.rsplit("}", 1)[-1] for e in tree.iter()]
    if "image" in tags or "foreignObject" in tags or "data:image" in path.read_text(encoding="utf-8"):
        raise ValueError("Bitmap/foreignObject embedding does not qualify")
    if not tags.count("path"):
        raise ValueError("No vector paths")
    commands = "".join(e.get("d", "") for e in tree.iter())
    return {"path_count": tags.count("path"), "subpaths": len(re.findall(r"[Mm]", commands)), "path_commands": len(re.findall(r"[MmLlHhVvCcSsQqTtAaZz]", commands)), "bytes": path.stat().st_size, "image_elements": 0}


def exact_runs(source, output):
    """Lossless pixel geometry ceiling: merge equal horizontal runs vertically.

    These are vector rectangles, NOT smooth curves or semantic objects. All
    rectangles/subpaths must be counted; palette path count alone is misleading.
    """
    a = np.asarray(source, dtype=np.uint32)
    packed = (a[:, :, 0] << 16) | (a[:, :, 1] << 8) | a[:, :, 2]
    active, completed = {}, {}
    def finish(key, span):
        x0, x1, color = key
        y0, y1 = span
        completed.setdefault(color, []).append(f"M{x0},{y0}h{x1-x0}v{y1-y0}h{x0-x1}z")
    for y, row in enumerate(packed):
        breaks = np.r_[0, np.flatnonzero(row[1:] != row[:-1]) + 1, len(row)]
        current = {}
        for x0, x1 in zip(breaks[:-1], breaks[1:]):
            key = (int(x0), int(x1), int(row[x0]))
            previous = active.pop(key, None)
            current[key] = (previous[0] if previous else y, y + 1)
        for key, span in active.items():
            finish(key, span)
        active = current
    for key, span in active.items():
        finish(key, span)
    root = ET.Element(f"{{{SVG}}}svg", width=str(source.width), height=str(source.height),
                      viewBox=f"0 0 {source.width} {source.height}", attrib={"shape-rendering": "crispEdges"})
    for color, paths in sorted(completed.items()):
        ET.SubElement(root, f"{{{SVG}}}path", fill=f"#{color:06x}", d="".join(paths))
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--colors", type=int, nargs="+", default=[16, 32, 64])
    parser.add_argument("--modes", nargs="+", default=["polygon", "spline"])
    parser.add_argument("--layer-difference", type=int, default=16)
    parser.add_argument("--speckle", type=int, default=4)
    parser.add_argument("--crisp", action="store_true")
    parser.add_argument("--inkscape", default=r"C:\Program Files\Inkscape\bin\inkscape.com")
    args = parser.parse_args()
    if args.input.suffix.lower() not in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        parser.error("input must be a bitmap")
    args.output.mkdir(parents=True, exist_ok=False)
    with Image.open(args.input) as raw:
        source = raw.convert("RGBA")
        if source.getchannel("A").getextrema() != (255, 255):
            raise ValueError("This initial probe requires opaque input; do not silently discard alpha")
        source = source.convert("RGB")
    source.save(args.output / "original.png")
    report = {
        "input": str(args.input.resolve()), "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "size": source.size, "resized": False, "semantic_editability_tested": False,
        "versions": {n: importlib.metadata.version(n) for n in ("vtracer", "numpy", "Pillow", "eas-hmi")},
        "method": "Agent orchestrates deterministic tracing; tracer sees only quantized PNG. No matching original SVG/model is used.",
        "variants": [], "commands": [], "thresholds_are_provisional": True,
        "trace_parameters": {"layer_difference": args.layer_difference, "filter_speckle": args.speckle, "crisp_edges": args.crisp},
        "gate": "Against quantized input: RGB MAE<=2; >=97% pixels max channel error<=8; edge F1(1px)>=0.97; <=20000 paths and <=5MB EAS SVG. Human review of text/lines still required.",
    }
    def run(name, command):
        started = time.perf_counter()
        result = subprocess.run(list(map(str, command)), capture_output=True, timeout=180, check=False,
                                env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1"))
        (args.output / (name + ".stdout")).write_bytes(result.stdout)
        (args.output / (name + ".stderr")).write_bytes(result.stderr)
        report["commands"].append({"name": name, "argv": list(map(str, command)),
                                   "exit_code": result.returncode, "seconds": time.perf_counter() - started})
        save(args.output / "report.json", report)
        if result.returncode:
            raise RuntimeError(f"{name} failed: {result.returncode}; see stderr, no automatic retry")

    def raster(name, svg, png):
        run(name, [args.inkscape, svg, "--export-area-page", "--export-type=png",
                   f"--export-width={source.width}", f"--export-height={source.height}",
                   "--export-png-color-mode=RGBA_8", f"--export-filename={png}"])

    for colors in args.colors:
        reduced = source.quantize(colors=colors, method=Image.Quantize.MEDIANCUT,
                                  dither=Image.Dither.NONE).convert("RGB")
        reduced_path = args.output / f"quantized-{colors}.png"
        reduced.save(reduced_path)
        for mode in args.modes:
            name = f"c{colors}-{mode}"
            folder = args.output / name
            folder.mkdir()
            trace = folder / "trace.svg"
            started = time.perf_counter()
            if mode == "exact-runs":
                exact_runs(reduced, trace)
            else:
                vtracer.convert_image_to_svg_py(str(reduced_path), str(trace), colormode="color",
                    hierarchical="stacked", mode=mode, filter_speckle=args.speckle, color_precision=8,
                    layer_difference=args.layer_difference, path_precision=3)
            if args.crisp:
                crisp_tree = ET.parse(trace)
                crisp_tree.getroot().set("shape-rendering", "crispEdges")
                crisp_tree.write(trace, encoding="utf-8", xml_declaration=True)
            tracing_seconds = time.perf_counter() - started
            trace_stats = inspect_svg(trace)
            # One vector group avoids the current importer's quadratic copying
            # of thousands of top-level paths. Paths remain vector artwork.
            root = ET.parse(trace).getroot()
            group = ET.Element(f"{{{SVG}}}g", {"id": "traced-artwork"})
            for child in list(root):
                root.remove(child)
                group.append(child)
            root.append(group)
            grouped = folder / "grouped.svg"
            ET.ElementTree(root).write(grouped, encoding="utf-8", xml_declaration=True)
            state = folder / "project"
            cli = [sys.executable, "-X", "faulthandler", "-m", "eas_hmi.cli", "--project", state]
            run(name + "-import", [*cli, "import-svg", grouped])
            final = folder / "eas.svg"
            run(name + "-render", [*cli, "render", "--output", final])
            run(name + "-validate", [*cli, "validate", "--strict"])
            direct_png, final_png = folder / "trace.png", folder / "eas.png"
            raster(name + "-direct-png", trace, direct_png)
            raster(name + "-eas-png", final, final_png)
            actual, direct = Image.open(final_png).convert("RGB"), Image.open(direct_png).convert("RGB")
            q_metrics = metrics(reduced, actual)
            final_stats = inspect_svg(final)
            row = {"name": name, "colors": colors, "mode": mode, "tracing_seconds": tracing_seconds,
                   "pixel_geometry_ceiling_only": mode == "exact-runs",
                   "trace": trace_stats, "eas_svg": final_stats,
                   "quantization_vs_original": metrics(source, reduced),
                   "eas_vs_quantized": q_metrics, "eas_vs_original": metrics(source, actual),
                   "eas_vs_direct_trace": metrics(direct, actual),
                   "eas_identical_to_direct_trace": np.array_equal(np.array(direct), np.array(actual)),
                   "provisional_numeric_gate": mode != "exact-runs" and q_metrics["rgb_mae_0_255"] <= 2
                    and q_metrics["pixels_max_channel_error_le_8_percent"] >= 97
                    and q_metrics["edge_f1_1px"] >= .97
                    and final_stats["path_count"] <= 20000 and final_stats["bytes"] <= 5_000_000}
            report["variants"].append(row)
            save(args.output / "report.json", report)
            print(json.dumps(row), flush=True)
    report["status"] = "probe_complete"
    save(args.output / "report.json", report)


if __name__ == "__main__":
    main()
