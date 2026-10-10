"""Declared EAS-HMI interface used by sibling EAFS and Visual packages.

This module re-exports the owning implementations; it holds no project state
and does not create another mutation path.
"""

from .agent_protocol import FORMAT, PROTOCOL, apply_steps, parse_plan
from .agent_protocol import contract as plan_contract
from .errors import EngineeringError
from .export.assets import InkscapeBackend
from .model import Node, canonical_hash
from .model.core import canonical_json
from .operations.transaction import Store
from .query import Query
from .svg.importer import import_svg
from .svg.renderer import render

__all__ = (
    "FORMAT",
    "PROTOCOL",
    "EngineeringError",
    "InkscapeBackend",
    "Node",
    "Query",
    "Store",
    "apply_steps",
    "canonical_hash",
    "canonical_json",
    "import_svg",
    "parse_plan",
    "plan_contract",
    "render",
)
