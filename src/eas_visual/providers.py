"""Provider contracts; no model or network SDK is a core dependency."""

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from PIL import Image

from .errors import require
from .pixels import read_image, validate_box


@dataclass
class PatchResult:
    image: Image.Image
    metadata: dict
    warnings: list[str]


class ImageEditProvider(Protocol):
    def edit_patch(self, context: Image.Image, mask: np.ndarray, instruction: str) -> PatchResult: ...
    def capabilities(self) -> dict: ...
    def metadata(self) -> dict: ...


class MockProvider:
    def capabilities(self):
        return {"local_patch": True, "natural_language_understanding": False}

    def metadata(self):
        return {"provider": "mock", "model": None, "seed": None, "deterministic": True}

    def edit_patch(self, context, mask, instruction):
        values = np.clip(np.asarray(context, dtype=np.int16) + [3, -2, 1], 0, 255).astype(np.uint8)
        values[mask] = [45, 120, 210]
        return PatchResult(
            Image.fromarray(values),
            self.metadata(),
            ["MOCK: paints blue; does not understand the instruction"],
        )


class ExternalPatchProvider:
    """A current/future image model is invoked by the host Agent, then submitted here."""

    def __init__(self, data, returned_metadata, normalize=False):
        require(
            isinstance(returned_metadata, dict) and bool(returned_metadata.get("provider")),
            "PROVIDER_METADATA",
            "Real returned provider name/metadata required; use null for unknown model/seed",
        )
        self.data, self.returned_metadata, self.normalize = data, returned_metadata, normalize

    def capabilities(self):
        return {"local_patch": True, "transport": "saved-result exchange", "invokes_model": False}

    def metadata(self):
        return {"adapter": "external-patch", "returned": self.returned_metadata}

    def edit_patch(self, context, mask, instruction):
        image = read_image(self.data)
        warnings, original_size = [], image.size
        if image.size != context.size:
            require(
                self.normalize,
                "PATCH_SIZE",
                f"Expected {context.size}, got {image.size}; explicit normalize required",
            )
            image = image.resize(context.size, Image.Resampling.LANCZOS)
            warnings.append(
                "Provider patch explicitly normalized with LANCZOS; alignment/quality needs review"
            )
        return PatchResult(
            image,
            {
                **self.metadata(),
                "raw_size": list(original_size),
                "normalized_size": list(image.size),
                "normalized": original_size != image.size,
            },
            warnings,
        )


class BackgroundRepairProvider(Protocol):
    def repair(self, image: Image.Image, mask: np.ndarray) -> PatchResult: ...


class DonorRepair:
    def __init__(self, donor_bbox):
        self.donor_bbox = donor_bbox

    def repair(self, image, mask):
        x0, y0, x1, y1 = validate_box(self.donor_bbox, image.size)
        require(not mask[y0:y1, x0:x1].any(), "INVALID_DONOR", "Donor overlaps repair mask")
        source = np.asarray(image)
        donor = source[y0:y1, x0:x1]
        result = source.copy()
        yy, xx = np.where(mask)
        result[yy, xx] = donor[(yy - y0) % (y1 - y0), (xx - x0) % (x1 - x0)]
        return PatchResult(
            Image.fromarray(result),
            {"repair": "donor", "donor_bbox": self.donor_bbox},
            ["quality_warning: tiled donor is an estimate; hidden background/seams not guaranteed"],
        )


class NoRepair:
    def repair(self, image, mask):
        return PatchResult(
            image.copy(),
            {"repair": "none"},
            ["quality_warning: explicit repair=none retains source pixels (duplicate-placement diagnostic)"],
        )
