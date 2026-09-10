from ..model import Project


def objects(project: Project | None):
    if project is None:
        return {}
    result = {}
    for kind, rows in [
        ("node", project.all_nodes()),
        ("equipment", project.equipment),
        ("point", project.points),
        ("template", project.templates),
    ]:
        for row in rows:
            result[f"{kind}:{row.id}"] = row.model_dump(mode="json")
    for page in project.pages:
        result[f"page:{page.id}"] = page.model_dump(mode="json", exclude={"nodes"})
        result[f"page:{page.id}"]["node_order"] = [n.id for n in page.nodes]
    result[f"project:{project.id}"] = project.model_dump(
        mode="json", exclude={"revision", "pages", "equipment", "points", "templates"}
    )
    result[f"project:{project.id}"]["registry_order"] = {
        key: [row.id for row in getattr(project, key)]
        for key in ("pages", "equipment", "points", "templates")
    }
    return result


def flatten(value, prefix=""):
    if isinstance(value, dict) and value:
        out = {}
        for key, item in sorted(value.items()):
            key = key.replace("\\", "\\\\").replace(".", "\\.")
            out.update(flatten(item, f"{prefix}.{key}" if prefix else key))
        return out
    return {prefix: value}


def semantic_diff(before: Project | None, after: Project):
    left, right = objects(before), objects(after)
    result = []
    for key in sorted(left.keys() | right.keys()):
        if left.get(key) == right.get(key):
            continue
        kind, object_id = key.split(":", 1)
        if key not in left or key not in right:
            changes = {"object": {"old": left.get(key), "new": right.get(key)}}
        else:
            a, b = dict(left[key]), dict(right[key])
            before_roles = [v["role"] for v in a.get("bindings", [])]
            after_roles = [v["role"] for v in b.get("bindings", [])]
            for row in (a, b):
                if "bindings" in row:
                    row["bindings"] = {
                        binding["role"]: {k: v for k, v in binding.items() if k != "role"}
                        for binding in row["bindings"]
                    }
            af, bf = flatten(a), flatten(b)
            changes = {}
            if before_roles != after_roles and sorted(before_roles) == sorted(after_roles):
                changes["bindings.role_order"] = {"old": before_roles, "new": after_roles}
            for field in sorted(af.keys() | bf.keys()):
                if af.get(field) != bf.get(field) or (field in af) != (field in bf):
                    name = (
                        f"geometry.{field}" if field in ("x", "y", "width", "height", "rotation") else field
                    )
                    changes[name] = {"old": af.get(field), "new": bf.get(field)}
                    if (field in af) != (field in bf):
                        changes[name].update(old_present=field in af, new_present=field in bf)
        result.append({"object_id": object_id, "object_type": kind, "changes": changes})
    return result
