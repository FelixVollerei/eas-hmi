"""Public Python API for immutable scenes, bounded edits and reversible history."""

import copy
import json
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from filelock import FileLock, Timeout
from PIL import Image

from eas_hmi.errors import EngineeringError
from eas_hmi.export.assets import InkscapeBackend
from eas_hmi.model import Node
from eas_hmi.svg.importer import import_svg
from eas_hmi.svg.renderer import render

from .errors import VisualError, require
from .pixels import (
    box,
    dilate,
    mask_png,
    metrics,
    pixel_hash,
    png,
    read_image,
    read_mask,
    selection,
    translated,
    validate_box,
    vectorize,
)
from .providers import DonorRepair, ExternalPatchProvider, MockProvider, NoRepair
from .storage import Storage, encoded, sha


def now():
    return datetime.now(UTC).isoformat()


class VisualProject:
    def __init__(self, path, inkscape=None):
        self.store = Storage(path)
        self.inkscape = inkscape or os.environ.get("EAS_VISUAL_INKSCAPE")

    @contextmanager
    def session(self):
        require(self.store.root.is_dir(), "NOT_A_PROJECT", "Project directory does not exist")
        try:
            with FileLock(self.store.path(".visual.lock"), timeout=5):
                try:
                    manifest, records = self.store.load()
                except VisualError:
                    raise
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    raise VisualError("CORRUPT_PROJECT", f"Cannot verify project: {exc}") from exc
                yield manifest, records, records[manifest["current_revision"]]
        except Timeout as exc:
            raise VisualError("PROJECT_BUSY", "Another operation holds the project lock") from exc

    def raster(self, data, size):
        try:
            backend = InkscapeBackend(self.inkscape, timeout=180)
            image, warning = backend.png(data, *size)
            require(
                image.getchannel("A").getextrema() == (255, 255),
                "RENDER_ALPHA",
                "Renderer introduced transparency",
            )
            return image.convert("RGB"), warning
        except EngineeringError as exc:
            raise VisualError("RENDER_FAILED", str(exc)) from exc

    @classmethod
    def create(cls, input_path, output, colors=64, inkscape=None):
        require(type(colors) is int and 2 <= colors <= 256, "INVALID_COLORS", "colors must be 2..256")
        destination = Path(output).absolute()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(destination) + ".init.lock", timeout=5):
            require(not destination.exists(), "ALREADY_EXISTS", "Project output already exists")
            stage = destination.with_name("." + destination.name + ".init-" + uuid.uuid4().hex)
            stage.mkdir()
            project = cls(stage, inkscape)
            source_data = Path(input_path).read_bytes()
            original = read_image(source_data, opaque=False)
            white = Image.new("RGBA", original.size, "white")
            white.alpha_composite(original)
            normalized = white.convert("RGB")
            baseline = normalized.quantize(
                colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
            ).convert("RGB")
            plain, _ = vectorize(baseline, np.ones((baseline.height, baseline.width), dtype=bool))
            eas = import_svg(plain).project
            eas.pages[0].nodes[0].locked = True
            baseline_svg = render(eas, eas.pages[0].id)
            rendered, warning = project.raster(baseline_svg, baseline.size)
            require(
                pixel_hash(rendered) == pixel_hash(baseline),
                "BASELINE_MISMATCH",
                "Baseline rendering is not pixel exact",
            )
            store = project.store
            baseline_refs = {
                "original": store.asset("baseline", source_data, "source"),
                "input_png": store.asset("baseline", png(original), "png"),
                "png": store.asset("baseline", png(baseline), "png"),
                "svg": store.asset("baseline", baseline_svg, "svg"),
            }
            record = {
                "edit_id": "0000-baseline",
                "parent": None,
                "timestamp": now(),
                "operation": "init",
                "target_object": None,
                "mask": None,
                "editable_support": None,
                "source_hash": sha(source_data),
                "provider": None,
                "instruction": None,
                "transform": None,
                "patch_reference": None,
                "baseline": baseline_refs,
                "state": {"objects": {}, "layers": []},
                "result": baseline_refs["png"],
                "svg": baseline_refs["svg"],
                "result_hash": pixel_hash(baseline),
                "validation": {"baseline_pixel_exact": True},
                "warnings": [warning] if warning else [],
            }
            history_ref = store.asset("history", encoded(record), "json")
            manifest = {
                "version": 1,
                "tool_version": "0.1.0",
                "baseline": baseline_refs,
                "baseline_hash": pixel_hash(baseline),
                "dimensions": list(baseline.size),
                "normalization": {"colors": colors, "alpha": "explicit white composite", "resized": False},
                "current_revision": record["edit_id"],
                "genesis": record["edit_id"],
                "counter": 0,
                "revisions": {record["edit_id"]: history_ref},
                "redo": [],
                "requests": {},
                "provider_metadata": [],
                "scene_reference": history_ref,
                "navigation": [],
                "max_edit_fraction": 0.25,
            }
            store.write_manifest(manifest)
            os.rename(stage, destination)
        return cls(destination, inkscape).inspect()

    def summary(self, manifest, record):
        return {
            "version": "0.1.0",
            "project": str(self.store.root),
            "dimensions": manifest["dimensions"],
            "revision": record["edit_id"],
            "baseline_hash": manifest["baseline_hash"],
            "output_hash": record["result_hash"],
            "objects": record["state"]["objects"],
            "revisions": list(manifest["revisions"]),
            "redo": manifest["redo"],
            "output_png": str(self.store.path(record["result"]["path"])),
            "output_svg": str(self.store.path(record["svg"]["path"])),
            "validation": record["validation"],
            "quality_warnings": record["warnings"],
        }

    def inspect(self):
        with self.session() as (manifest, _, head):
            return self.summary(manifest, head)

    def current_image(self, head):
        return read_image(self.store.read(head["result"]))

    def object(self, head, object_id):
        objects = head["state"]["objects"]
        if object_id in objects:
            return object_id, objects[object_id]
        matches = [(key, value) for key, value in objects.items() if value["semantic_label"] == object_id]
        require(len(matches) == 1, "OBJECT_NOT_FOUND", "Use an existing unique object ID or label")
        return matches[0]

    def check_support(self, manifest, head, support, selected_id=None):
        require(
            support.any() and support.mean() <= manifest["max_edit_fraction"],
            "INVALID_SUPPORT",
            "Edit must be nonempty and at most 25% of the canvas in v0.1",
        )
        for key, obj in head["state"]["objects"].items():
            if key != selected_id:
                other = read_mask(self.store.read(obj["mask"]), manifest["dimensions"])
                require(
                    not (support & other).any(), "OBJECT_CONFLICT", f"Edit overlaps promoted object {key}"
                )

    def layer(self, state, image, mask, role):
        data, bbox = vectorize(image, mask)
        layer = {
            "asset": self.store.asset("patches", data, "svg"),
            "bbox": bbox,
            "role": role,
            "node_id": "layer-" + uuid.uuid4().hex[:12],
        }
        state["layers"].append(layer)
        return layer

    def compose(self, manifest, state):
        project = import_svg(self.store.read(manifest["baseline"]["svg"])).project
        page = project.pages[0]
        for i, layer in enumerate(state["layers"]):
            x0, y0, x1, y1 = validate_box(layer["bbox"], manifest["dimensions"])
            page.nodes.append(
                Node(
                    id=layer["node_id"],
                    kind="opaque_svg",
                    page_id=page.id,
                    x=x0,
                    y=y0,
                    width=x1 - x0,
                    height=y1 - y0,
                    content_width=x1 - x0,
                    content_height=y1 - y0,
                    z_index=i + 1,
                    payload=self.store.read(layer["asset"]).decode("utf-8"),
                )
            )
        svg = render(project, page.id)
        actual, warning = self.raster(svg, manifest["dimensions"])
        return svg, actual, warning

    def commit(self, manifest, head, state, operation, support, expected, **provenance):
        previous = self.current_image(head)
        if operation == "promote":
            svg, actual, warning = self.store.read(head["svg"]), previous, ""
        else:
            svg, actual, warning = self.compose(manifest, state)
        validation = metrics(previous, actual, support)
        require(
            validation["untouched_changed_pixels"] == 0,
            "OUTSIDE_CHANGE",
            "Renderer changed pixels outside declared support",
        )
        require(
            pixel_hash(actual) == pixel_hash(expected),
            "COMPOSITE_MISMATCH",
            "Renderer differs from planned composite",
        )
        if operation == "move":
            source_mask = read_mask(self.store.read(provenance["mask"]), manifest["dimensions"])
            yy, xx = np.where(source_mask)
            dx, dy = provenance["transform"]["dx"], provenance["transform"]["dy"]
            error = np.abs(
                np.asarray(previous, dtype=np.int16)[yy, xx]
                - np.asarray(actual, dtype=np.int16)[yy + dy, xx + dx]
            )
            provenance["object_appearance_mae"] = float(error.mean())
            require(not error.any(), "OBJECT_CHANGED", "Structural translation changed object pixels")
        edit_id = f"{manifest['counter'] + 1:04d}-" + uuid.uuid4().hex[:8]
        record = {
            "edit_id": edit_id,
            "parent": head["edit_id"],
            "timestamp": now(),
            "operation": operation,
            "state": state,
            "source_hash": head["result_hash"],
            "editable_support": self.store.asset("masks", mask_png(support), "png"),
            "result": self.store.asset("output", png(actual), "png"),
            "svg": self.store.asset("output", svg, "svg"),
            "result_hash": pixel_hash(actual),
            "validation": validation,
            "target_object": None,
            "mask": None,
            "provider": None,
            "instruction": None,
            "transform": None,
            "patch_reference": None,
            "warnings": [],
            **provenance,
        }
        if warning:
            record["warnings"].append(warning)
        ref = self.store.asset("history", encoded(record), "json")
        manifest["revisions"][edit_id] = ref
        manifest["current_revision"] = edit_id
        manifest["scene_reference"] = ref
        manifest["counter"] += 1
        manifest["redo"] = []
        if record["provider"]:
            manifest["provider_metadata"].append(record["provider"])
        self.store.write_manifest(manifest)  # sole commit point; previous state survives failures above
        return self.summary(manifest, record)

    def promote(self, label, bbox=None, polygon=None, mask_path=None):
        require(
            isinstance(label, str) and 0 < len(label) <= 120,
            "INVALID_LABEL",
            "Nonempty label up to 120 characters required",
        )
        with self.session() as (manifest, _, head):
            require(
                not any(o["semantic_label"] == label for o in head["state"]["objects"].values()),
                "DUPLICATE_LABEL",
                "Choose a unique label",
            )
            mask = selection(
                tuple(manifest["dimensions"]),
                bbox,
                polygon,
                Path(mask_path).read_bytes() if mask_path else None,
            )
            self.check_support(manifest, head, mask)
            state = copy.deepcopy(head["state"])
            current = self.current_image(head)
            bounds = box(mask)
            extracted = current.crop(bounds).convert("RGBA")
            extracted.putalpha(Image.fromarray(mask.astype(np.uint8) * 255).crop(bounds))
            object_id = "object-" + uuid.uuid4().hex[:8]
            obj = {
                "semantic_label": label,
                "bbox": bounds,
                "mask": self.store.asset("masks", mask_png(mask), "png"),
                "source_region": self.store.asset("objects", png(extracted), "png"),
                "source_hash": head["result_hash"],
                "transform": {"dx": 0, "dy": 0},
                "metadata": {"localization": "manual-or-agent-assisted; no automatic segmentation claim"},
            }
            state["objects"][object_id] = obj
            return self.commit(
                manifest,
                head,
                state,
                "promote",
                mask,
                current,
                target_object=object_id,
                mask=obj["mask"],
                instruction=f"Promote {label}",
            )

    def context(self, manifest, head, obj, margin, context_bbox):
        mask = read_mask(self.store.read(obj["mask"]), manifest["dimensions"])
        support = dilate(mask, margin)
        bounds = box(support)
        if context_bbox is None:
            x0, y0, x1, y1 = bounds
            context_bbox = [
                max(0, x0 - 16),
                max(0, y0 - 16),
                min(manifest["dimensions"][0], x1 + 16),
                min(manifest["dimensions"][1], y1 + 16),
            ]
        cx0, cy0, cx1, cy1 = validate_box(context_bbox, manifest["dimensions"])
        require(
            cx0 <= bounds[0] and cy0 <= bounds[1] and cx1 >= bounds[2] and cy1 >= bounds[3],
            "CONTEXT_TOO_SMALL",
            "Context must contain support including margin",
        )
        require(
            (cx1 - cx0) * (cy1 - cy0) < np.prod(manifest["dimensions"]),
            "WHOLE_IMAGE_PROVIDER",
            "Provider receives a crop, never the whole image",
        )
        return mask, support, context_bbox, self.current_image(head).crop(context_bbox)

    def prepare(self, object_id, instruction, margin=0, context_bbox=None, purpose="edit"):
        require(purpose in ("edit", "repair"), "INVALID_PURPOSE", "Use edit or repair")
        require(
            isinstance(instruction, str) and instruction.strip(),
            "INSTRUCTION_REQUIRED",
            "Provide an instruction",
        )
        with self.session() as (manifest, _, head):
            key, obj = self.object(head, object_id)
            mask, support, bounds, context = self.context(manifest, head, obj, margin, context_bbox)
            self.check_support(manifest, head, support, key)
            x0, y0, x1, y1 = bounds
            request = {
                "request_id": uuid.uuid4().hex,
                "revision": head["edit_id"],
                "source_hash": head["result_hash"],
                "target_object": key,
                "purpose": purpose,
                "instruction": instruction,
                "margin": margin,
                "context_bbox": bounds,
                "context": self.store.asset("patches", png(context), "png"),
                "mask": self.store.asset("masks", mask_png(mask[y0:y1, x0:x1]), "png"),
                "editable_support": self.store.asset("masks", mask_png(support), "png"),
            }
            ref = self.store.asset("patches", encoded(request), "json")
            manifest["requests"][request["request_id"]] = ref
            self.store.write_manifest(manifest)
            return {
                **request,
                "request_file": str(self.store.path(ref["path"])),
                "context_file": str(self.store.path(request["context"]["path"])),
                "mask_file": str(self.store.path(request["mask"]["path"])),
            }

    def external(self, manifest, head, key, request_id, patch_path, metadata, normalize, purpose):
        require(request_id in manifest["requests"], "REQUEST_NOT_FOUND", "Prepare a local request first")
        request = json.loads(self.store.read(manifest["requests"][request_id]))
        require(
            request["revision"] == head["edit_id"] and request["source_hash"] == head["result_hash"],
            "STALE_REQUEST",
            "Scene changed after provider request; prepare again",
        )
        require(
            request["target_object"] == key and request["purpose"] == purpose,
            "REQUEST_MISMATCH",
            "Wrong object or purpose",
        )
        require(patch_path is not None, "PATCH_REQUIRED", "Provide the returned local patch file")
        data = Path(patch_path).read_bytes()
        context = read_image(self.store.read(request["context"]))
        local_mask = read_mask(self.store.read(request["mask"]), context.size)
        result = ExternalPatchProvider(data, metadata, normalize).edit_patch(
            context, local_mask, request["instruction"]
        )
        result.metadata["raw_output"] = self.store.asset("patches", data, "png")
        result.metadata["normalized_output"] = self.store.asset("patches", png(result.image), "png")
        result.metadata["request"] = manifest["requests"][request_id]
        return request, result

    def patch_composite(self, image, mask, support, candidate, bounds, margin):
        x0, y0, x1, y1 = bounds
        alpha = mask.astype(float)
        for radius in range(1, margin + 1):
            ring = dilate(mask, radius) & ~dilate(mask, radius - 1)
            alpha[ring] = (margin + 1 - radius) / (margin + 1)
        values = np.asarray(image).copy()
        local_alpha = alpha[y0:y1, x0:x1, None]
        mixed = np.rint(
            values[y0:y1, x0:x1] * (1 - local_alpha) + np.asarray(candidate) * local_alpha
        ).astype(np.uint8)
        values[y0:y1, x0:x1] = mixed
        result = Image.fromarray(values)
        require(
            metrics(image, result, support)["untouched_changed_pixels"] == 0,
            "OUTSIDE_CHANGE",
            "Patch isolation failed",
        )
        return result

    def edit(
        self,
        object_id,
        instruction="",
        provider="mock",
        margin=0,
        context_bbox=None,
        request_id=None,
        patch_path=None,
        metadata=None,
        normalize=False,
    ):
        with self.session() as (manifest, _, head):
            key, obj = self.object(head, object_id)
            current = self.current_image(head)
            if provider == "external-patch":
                request, result = self.external(
                    manifest, head, key, request_id, patch_path, metadata, normalize, "edit"
                )
                instruction, margin, context_bbox = (
                    request["instruction"],
                    request["margin"],
                    request["context_bbox"],
                )
            else:
                require(provider == "mock", "UNKNOWN_PROVIDER", "Use mock or external-patch")
                require(bool(instruction.strip()), "INSTRUCTION_REQUIRED", "Provide instruction")
            mask, support, bounds, context = self.context(manifest, head, obj, margin, context_bbox)
            self.check_support(manifest, head, support, key)
            x0, y0, x1, y1 = bounds
            local_mask = mask[y0:y1, x0:x1]
            if provider == "mock":
                result = MockProvider().edit_patch(context, local_mask, instruction)
                result.metadata["normalized_output"] = self.store.asset("patches", png(result.image), "png")
                result.metadata["context"] = self.store.asset("patches", png(context), "png")
            result.metadata["context_outside_mask_before_composite"] = metrics(
                context, result.image, local_mask
            )
            expected = self.patch_composite(current, mask, support, result.image, bounds, margin)
            state = copy.deepcopy(head["state"])
            layer = self.layer(state, expected, support, "replacement")
            state["objects"][key]["last_instruction"] = instruction
            return self.commit(
                manifest,
                head,
                state,
                "edit",
                support,
                expected,
                target_object=key,
                mask=obj["mask"],
                provider=result.metadata,
                instruction=instruction,
                patch_reference=layer["asset"],
                warnings=result.warnings,
                context_bbox=bounds,
                blending_margin=margin,
            )

    def move(
        self,
        object_id,
        dx,
        dy,
        repair="donor",
        donor_bbox=None,
        margin=0,
        request_id=None,
        patch_path=None,
        metadata=None,
        normalize=False,
    ):
        with self.session() as (manifest, _, head):
            key, obj = self.object(head, object_id)
            current = self.current_image(head)
            mask = read_mask(self.store.read(obj["mask"]), manifest["dimensions"])
            target = translated(mask, dx, dy)
            repair_mask = dilate(mask, margin)
            if repair == "generative":
                request, result = self.external(
                    manifest, head, key, request_id, patch_path, metadata, normalize, "repair"
                )
                margin = request["margin"]
                repair_mask = dilate(mask, margin)
                repaired = self.patch_composite(
                    current, mask, repair_mask, result.image, request["context_bbox"], margin
                )
                result.warnings.append(
                    "quality_warning: generative background is an estimate, not hidden ground truth"
                )
            elif repair == "donor":
                require(
                    donor_bbox is not None,
                    "REPAIR_REQUIRED",
                    "Provide --donor bbox, generative repair, or explicit repair=none",
                )
                result = DonorRepair(donor_bbox).repair(current, repair_mask)
                repaired = result.image
            else:
                require(repair == "none", "UNKNOWN_REPAIR", "Use donor, generative or explicit none")
                result = NoRepair().repair(current, repair_mask)
                repaired = result.image
            support = repair_mask | target
            self.check_support(manifest, head, support, key)
            state = copy.deepcopy(head["state"])
            repair_layer = self.layer(state, repaired, repair_mask, "repair")
            source_layer = self.layer(state, current, mask, "moved-object")
            source_layer["bbox"] = [
                v + (dx if i % 2 == 0 else dy) for i, v in enumerate(source_layer["bbox"])
            ]
            values = np.asarray(repaired).copy()
            yy, xx = np.where(mask)
            values[yy + dy, xx + dx] = np.asarray(current)[yy, xx]
            expected = Image.fromarray(values)
            state["objects"][key]["mask"] = self.store.asset("masks", mask_png(target), "png")
            state["objects"][key]["bbox"] = box(target)
            state["objects"][key]["transform"]["dx"] += dx
            state["objects"][key]["transform"]["dy"] += dy
            result.metadata["repair_mask"] = self.store.asset("masks", mask_png(repair_mask), "png")
            return self.commit(
                manifest,
                head,
                state,
                "move",
                support,
                expected,
                target_object=key,
                mask=obj["mask"],
                provider=result.metadata,
                instruction=f"Move {obj['semantic_label']} by ({dx},{dy})",
                transform={"dx": dx, "dy": dy},
                patch_reference=[repair_layer["asset"], source_layer["asset"]],
                warnings=result.warnings,
            )

    def navigate(self, operation, revision=None):
        with self.session() as (manifest, records, head):
            if operation == "undo":
                require(head["parent"] is not None, "NOTHING_TO_UNDO", "Already at baseline")
                manifest["redo"].append(head["edit_id"])
                destination = head["parent"]
            elif operation == "redo":
                require(bool(manifest["redo"]), "NOTHING_TO_REDO", "No redo revision")
                destination = manifest["redo"].pop()
                require(
                    records[destination]["parent"] == head["edit_id"],
                    "INVALID_REDO",
                    "Redo is not a child of current revision",
                )
            else:
                require(operation in ("reset", "checkout"), "INVALID_OPERATION", operation)
                destination = manifest["genesis"] if operation == "reset" else revision
                require(destination in records, "REVISION_NOT_FOUND", "Unknown revision")
                manifest["redo"] = []
            manifest["navigation"].append(
                {"timestamp": now(), "operation": operation, "from": head["edit_id"], "to": destination}
            )
            manifest["current_revision"] = destination
            manifest["scene_reference"] = manifest["revisions"][destination]
            self.store.write_manifest(manifest)
            return self.summary(manifest, records[destination])

    def replay(self):
        with self.session() as (manifest, records, _):
            results = []
            for record in records.values():
                _, actual, _ = self.compose(manifest, record["state"])
                require(pixel_hash(actual) == record["result_hash"], "REPLAY_MISMATCH", record["edit_id"])
                results.append(
                    {"revision": record["edit_id"], "output_hash": pixel_hash(actual), "matches": True}
                )
            return {"revisions": results, "provider_calls": 0, "manifest_changed": False}

    def export(self, output):
        destination = Path(output).resolve()
        require(
            not destination.is_relative_to(self.store.root),
            "PROTECTED_OUTPUT",
            "Export outside project; immutable internal files cannot be overwritten",
        )
        require(not destination.exists(), "OUTPUT_EXISTS", "Refusing to overwrite an existing file")
        require(destination.suffix.lower() in (".png", ".svg"), "OUTPUT_FORMAT", "Use .png or .svg")
        with self.session() as (_, _, head):
            data = self.store.read(head["svg" if destination.suffix.lower() == ".svg" else "result"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(data)
            return {
                "output": str(destination),
                "revision": head["edit_id"],
                "output_hash": head["result_hash"],
            }

    def diff(self):
        with self.session() as (_, records, head):
            return {
                "revision": head["edit_id"],
                "parent": head["parent"],
                "operation": head["operation"],
                "validation": head["validation"],
                "source_hash": head["source_hash"],
                "result_hash": head["result_hash"],
                "editable_support": head["editable_support"],
                "parent_output": records[head["parent"]]["result"] if head["parent"] else None,
            }
