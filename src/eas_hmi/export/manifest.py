"""Deterministic binding rows; no writes or transaction side effects."""

import csv
import io

from ..errors import EngineeringError
from ..model.core import canonical_json
from ..validation import validate

FIELDS = (
    "page",
    "node_id",
    "node_kind",
    "equipment_id",
    "equipment_type",
    "binding_role",
    "point_id",
    "point_tag",
    "datatype",
    "unit",
)


def binding_rows(project):
    errors = [i for i in validate(project) if i["severity"] == "ERROR"]
    if errors:
        raise EngineeringError("VALIDATION_FAILED", "Cannot export invalid bindings", errors)
    equipment = {e.id: e for e in project.equipment}
    points = {p.id: p for p in project.points}
    rows = []
    for node in sorted(project.all_nodes(), key=lambda n: (n.page_id, n.id)):
        for binding in sorted(node.bindings, key=lambda b: b.role):
            point = points[binding.point_ref]
            item = equipment.get(node.equipment_ref)
            rows.append(
                dict(
                    zip(
                        FIELDS,
                        (
                            node.page_id,
                            node.id,
                            node.kind,
                            node.equipment_ref or "",
                            item.type if item else "",
                            binding.role,
                            point.id,
                            point.tag,
                            point.datatype,
                            point.unit,
                        ),
                    )
                )
            )
    return rows


def manifest_files(project):
    rows = binding_rows(project)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return {
        "bindings.csv": stream.getvalue().encode("utf-8-sig"),
        "bindings.json": canonical_json(rows).encode("utf-8"),
    }, len(rows)
