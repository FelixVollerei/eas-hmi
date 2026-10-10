"""A versioned, data-only edit plan for the EAS-HMI file profile."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import EngineeringError
from .model import Project, canonical_hash
from .operations import edit

PROTOCOL = "sfe/0.1"
FORMAT = "eas-hmi"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class BaseState(Record):
    revision: int = Field(ge=0)
    semantic_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class Move(Record):
    op: Literal["move"]
    ids: list[str] = Field(min_length=1)
    dx: float = 0
    dy: float = 0


class Set(Record):
    op: Literal["set"]
    ids: list[str] = Field(min_length=1)
    property: str
    value: Any


class Resize(Record):
    op: Literal["resize"]
    ids: list[str] = Field(min_length=1)
    width: float | None = None
    height: float | None = None


class Bind(Record):
    op: Literal["bind"]
    id: str
    role: str
    point_ref: str
    expression: str | None = None


class Unbind(Record):
    op: Literal["unbind"]
    id: str
    role: str


class Align(Record):
    op: Literal["align"]
    ids: list[str] = Field(min_length=1)
    mode: Literal["left", "center-x", "top", "center-y"]


class Distribute(Record):
    op: Literal["distribute"]
    ids: list[str] = Field(min_length=1)
    axis: Literal["x", "y"] = "x"
    gap: float = Field(default=20, ge=0)
    columns: int | None = Field(default=None, ge=1)


Step = Annotated[Move | Set | Resize | Bind | Unbind | Align | Distribute, Field(discriminator="op")]


class Plan(Record):
    protocol: Literal["sfe/0.1"]
    format: Literal["eas-hmi"]
    base: BaseState
    steps: list[Step] = Field(min_length=1)


def parse_plan(data: bytes) -> Plan:
    def reject_constant(value: str):
        raise ValueError(f"Non-JSON number: {value}")

    try:
        value = json.loads(data.decode("utf-8"), parse_constant=reject_constant)
        return Plan.model_validate(value)
    except (UnicodeError, ValueError, ValidationError) as exc:
        raise EngineeringError("INVALID_PLAN", "Edit plan is not valid sfe/0.1 JSON", str(exc)) from exc


def apply_steps(project: Project, plan: Plan) -> None:
    if project.revision != plan.base.revision or canonical_hash(project) != plan.base.semantic_hash:
        raise EngineeringError("BASE_CONFLICT", "Project no longer matches the plan base")
    for step in plan.steps:
        if isinstance(step, Move):
            edit.move(project, step.ids, step.dx, step.dy)
        elif isinstance(step, Set):
            edit.set_property(project, step.ids, step.property, step.value)
        elif isinstance(step, Resize):
            edit.resize(project, step.ids, step.width, step.height)
        elif isinstance(step, Bind):
            edit.bind(project, step.id, step.role, step.point_ref, step.expression)
        elif isinstance(step, Unbind):
            edit.unbind(project, step.id, step.role)
        elif isinstance(step, Align):
            edit.align(project, step.ids, step.mode)
        elif isinstance(step, Distribute):
            edit.distribute(project, step.ids, step.axis, step.gap, step.columns)


def contract() -> dict[str, Any]:
    return {
        "ok": True,
        "protocol": PROTOCOL,
        "format": FORMAT,
        "capabilities": [
            "status",
            "query",
            "inspect",
            "context",
            "preview",
            "apply",
            "diff",
            "validate",
            "undo",
        ],
        "plan_schema": Plan.model_json_schema(),
    }
