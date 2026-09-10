from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, validate_assignment=True, validate_default=True
    )


class Binding(Record):
    role: str
    point_ref: str
    expression: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Equipment(Record):
    id: str
    name: str
    type: str
    system: str = ""
    parent: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Point(Record):
    id: str
    tag: str
    datatype: str
    unit: str = ""
    equipment_ref: str | None = None
    role: str = "value"
    metadata: dict[str, Any] = Field(default_factory=dict)


class Template(Record):
    id: str
    required_bindings: list[str] = Field(default_factory=list)
    optional_bindings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Node(Record):
    id: str
    kind: Literal[
        "group",
        "text",
        "shape",
        "image_asset",
        "opaque_svg",
        "data_slot",
        "status_indicator",
        "equipment_card",
        "connection",
    ]
    page_id: str
    parent_id: str | None = None
    x: float = 0
    y: float = 0
    width: float = Field(default=100, ge=0)
    height: float = Field(default=60, ge=0)
    rotation: float = 0
    # Intrinsic coordinates stay fixed during a resize. Children use this local frame.
    content_width: float = Field(default=100, gt=0)
    content_height: float = Field(default=60, gt=0)
    z_index: int = 0
    visible: bool = True
    locked: bool = False
    style: dict[str, str | float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    equipment_ref: str | None = None
    bindings: list[Binding] = Field(default_factory=list)
    template_ref: str | None = None
    text: str = ""
    shape: Literal["rect", "ellipse", "line"] = "rect"
    payload: str | None = None
    image_uri: str | None = None
    source_ref: str | None = None
    target_ref: str | None = None


class Page(Record):
    id: str
    name: str
    width: float = Field(default=1920, gt=0)
    height: float = Field(default=1080, gt=0)
    background: str = "#111c2d"
    nodes: list[Node] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Project(Record):
    id: str
    name: str
    version: str = "0.1.0"
    revision: int = Field(default=0, ge=0)
    canvas_defaults: dict[str, float] = Field(default_factory=lambda: {"width": 1920, "height": 1080})
    pages: list[Page] = Field(default_factory=list)
    equipment: list[Equipment] = Field(default_factory=list)
    points: list[Point] = Field(default_factory=list)
    templates: list[Template] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def all_nodes(self) -> list[Node]:
        return [n for page in self.pages for n in page.nodes]


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        # Validate the raw JSON domain before Pydantic can coerce metadata NaN to null.
        value = value.model_dump(mode="python")
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def canonical_hash(project: Project) -> str:
    """Hash engineering state; exclude only the audit revision."""
    data = project.model_dump(mode="python")
    data.pop("revision")
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
