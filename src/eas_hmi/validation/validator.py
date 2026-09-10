from collections import Counter

from pydantic import ValidationError

from ..geometry import bounds
from ..model import Project
from ..model.core import canonical_json


def issue(code, object_id, message, severity="ERROR", suggested_action=None):
    return dict(
        code=code, severity=severity, object_id=object_id, message=message, suggested_action=suggested_action
    )


def validate_raw(data, **kwargs):
    try:
        canonical_json(data)
        return validate(Project.model_validate(data), **kwargs)
    except ValidationError as exc:
        return [
            issue("SCHEMA_VALIDATION", ".".join(map(str, e["loc"])), e["msg"])
            for e in exc.errors(include_url=False)
        ]
    except (ValueError, TypeError):
        return [issue("SCHEMA_VALIDATION", "project", "Input must contain finite, JSON-compatible values")]


def validate(project: Project, strict=False, out_of_bounds=None):
    issues = []
    nodes = project.all_nodes()
    ni = {n.id: n for n in nodes}
    ei = {e.id: e for e in project.equipment}
    pi = {p.id: p for p in project.points}
    ti = {t.id: t for t in project.templates}
    pages = {p.id: p for p in project.pages}
    for label, items in [
        ("node", nodes),
        ("page", project.pages),
        ("equipment", project.equipment),
        ("point", project.points),
        ("template", project.templates),
    ]:
        for object_id, count in Counter(x.id for x in items).items():
            if count > 1:
                issues.append(issue("DUPLICATE_ID", object_id, f"Duplicate {label} ID ({count})"))
            if not object_id or any(ch.isspace() for ch in object_id):
                issues.append(
                    issue("INVALID_ID", object_id, "IDs must be nonempty and contain no whitespace")
                )

    def hierarchy(items, index, attr, code):
        for obj in items:
            parent = getattr(obj, attr)
            if parent and parent not in index:
                issues.append(issue(code, obj.id, f"Unknown parent {parent}"))
            seen = {obj.id}
            while parent in index:
                if parent in seen:
                    issues.append(issue("CIRCULAR_HIERARCHY", obj.id, f"Hierarchy cycle at {parent}"))
                    break
                seen.add(parent)
                parent = getattr(index[parent], attr)

    hierarchy(nodes, ni, "parent_id", "INVALID_NODE_PARENT")
    hierarchy(project.equipment, ei, "parent", "INVALID_EQUIPMENT_PARENT")
    for template in project.templates:
        roles = template.required_bindings + template.optional_bindings
        if len(roles) != len(set(roles)) or any(not r or r.strip() != r for r in roles):
            issues.append(
                issue(
                    "INVALID_TEMPLATE_ROLES",
                    template.id,
                    "Required/optional roles must be unique, nonempty and disjoint",
                )
            )
    cyclic = {i["object_id"] for i in issues if i["code"] == "CIRCULAR_HIERARCHY"}
    severity = out_of_bounds or project.metadata.get("out_of_bounds_severity", "WARNING")
    if severity not in ("ERROR", "WARNING", "INFO"):
        issues.append(issue("INVALID_POLICY", project.id, "Invalid out-of-bounds severity"))
        severity = "ERROR"
    for page in project.pages:
        for n in page.nodes:
            if n.page_id not in pages or n.page_id != page.id:
                issues.append(
                    issue("INVALID_PAGE_REFERENCE", n.id, f"Node is not on its declared page {n.page_id}")
                )
            if n.parent_id in ni and ni[n.parent_id].page_id != n.page_id:
                issues.append(issue("INVALID_NODE_PARENT", n.id, "Parent must be on the same page"))
            if n.equipment_ref and n.equipment_ref not in ei:
                issues.append(
                    issue("INVALID_EQUIPMENT_REFERENCE", n.id, f"Unknown equipment {n.equipment_ref}")
                )
            if n.template_ref and n.template_ref not in ti:
                issues.append(issue("INVALID_TEMPLATE_REFERENCE", n.id, f"Unknown template {n.template_ref}"))
            for ref in (n.source_ref, n.target_ref):
                if ref and (ref not in ni or ni[ref].page_id != n.page_id):
                    issues.append(issue("INVALID_CONNECTION_REFERENCE", n.id, f"Invalid endpoint {ref}"))
            roles = Counter(b.role for b in n.bindings)
            for role, count in roles.items():
                if count > 1:
                    issues.append(issue("DUPLICATE_BINDING", n.id, f"Role {role} has {count} bindings"))
            for binding in n.bindings:
                if not binding.role or binding.role.strip() != binding.role:
                    issues.append(
                        issue(
                            "INVALID_BINDING_ROLE",
                            n.id,
                            "Binding role must be nonempty without surrounding whitespace",
                        )
                    )
                if binding.point_ref not in pi:
                    issues.append(
                        issue("INVALID_POINT_REFERENCE", n.id, f"Unknown point {binding.point_ref}")
                    )
            if n.template_ref in ti:
                template = ti[n.template_ref]
                for role in template.required_bindings:
                    if role not in roles:
                        issues.append(
                            issue(
                                "MISSING_REQUIRED_BINDING",
                                n.id,
                                f"Missing required binding {role}",
                                "ERROR" if strict else "WARNING",
                                f"Bind an existing {role} point",
                            )
                        )
                for role in roles:
                    if role not in template.required_bindings + template.optional_bindings:
                        issues.append(
                            issue("BINDING_ROLE_NOT_ALLOWED", n.id, f"Template does not allow {role}")
                        )
            if n.id not in cyclic:
                left, top, right, bottom = bounds(n, ni)
                if left < -1e-6 or top < -1e-6 or right > page.width + 1e-6 or bottom > page.height + 1e-6:
                    issues.append(
                        issue("OUT_OF_BOUNDS", n.id, "Node transformed bounds exceed page", severity)
                    )
    for p in project.points:
        if p.equipment_ref and p.equipment_ref not in ei:
            issues.append(issue("INVALID_EQUIPMENT_REFERENCE", p.id, f"Unknown equipment {p.equipment_ref}"))
    for obj in [*project.equipment, *project.points, *project.templates]:
        if obj.metadata.get("unresolved_import"):
            issues.append(
                issue(
                    "UNRESOLVED_IMPORT",
                    obj.id,
                    "SVG has identity only; registry details need enrichment",
                    "WARNING",
                )
            )
    return sorted(issues, key=lambda i: (i["object_id"], i["code"], i["message"]))
