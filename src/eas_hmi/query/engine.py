from collections import defaultdict, deque

from ..errors import EngineeringError


def dump(obj):
    return obj.model_dump(mode="json") if obj else None


class Query:
    def __init__(self, project):
        self.project = project
        self.nodes = {n.id: n for n in project.all_nodes()}
        self.equipment = {e.id: e for e in project.equipment}
        self.points = {p.id: p for p in project.points}

    def select(
        self, kind=None, page=None, equipment_type=None, missing_binding=None, equipment=None, template=None
    ):
        return sorted(
            [
                n
                for n in self.nodes.values()
                if (kind is None or n.kind == kind)
                and (page is None or n.page_id == page)
                and (equipment is None or n.equipment_ref == equipment)
                and (template is None or n.template_ref == template)
                and (
                    equipment_type is None
                    or (
                        n.equipment_ref in self.equipment
                        and self.equipment[n.equipment_ref].type == equipment_type
                    )
                )
                and (missing_binding is None or all(b.role != missing_binding for b in n.bindings))
            ],
            key=lambda n: n.id,
        )

    def node(self, object_id):
        if object_id not in self.nodes:
            raise EngineeringError("OBJECT_NOT_FOUND", f"Node not found: {object_id}")
        return self.nodes[object_id]

    def inspect(self, object_id):
        if object_id in self.nodes:
            n = self.nodes[object_id]
            refs = [x.id for x in self.nodes.values() if object_id in (x.source_ref, x.target_ref)]
            return {
                "target": dump(n),
                "equipment": dump(self.equipment.get(n.equipment_ref)),
                "bindings": [dump(b) for b in n.bindings],
                "parent": dump(self.nodes.get(n.parent_id)),
                "children": [dump(x) for x in self.nodes.values() if x.parent_id == object_id],
                "references": sorted(refs),
            }
        if object_id in self.equipment:
            return {
                "target": dump(self.equipment[object_id]),
                "parent": dump(self.equipment.get(self.equipment[object_id].parent)),
                "children": [dump(e) for e in self.equipment.values() if e.parent == object_id],
                "nodes": [dump(n) for n in self.select(equipment=object_id)],
                "points": [dump(p) for p in self.points.values() if p.equipment_ref == object_id],
            }
        if object_id in self.points:
            return {
                "target": dump(self.points[object_id]),
                "references": [
                    n.id for n in self.nodes.values() if any(b.point_ref == object_id for b in n.bindings)
                ],
            }
        raise EngineeringError("OBJECT_NOT_FOUND", f"Object not found: {object_id}")

    def context(self, object_id, depth=1, limit=30):
        if not 0 <= depth <= 3 or not 1 <= limit <= 200:
            raise EngineeringError("INVALID_CONTEXT_LIMIT", "Depth must be 0..3; limit must be 1..200")
        # Context is a projection, never inspect() plus an unbounded payload.
        target = self.nodes.get(object_id) or self.equipment.get(object_id) or self.points.get(object_id)
        if target is None:
            raise EngineeringError("OBJECT_NOT_FOUND", f"Object not found: {object_id}")
        children, equipment_nodes, point_nodes, connections = (defaultdict(set) for _ in range(4))
        for n in self.nodes.values():
            children[n.parent_id].add(n.id)
            if n.equipment_ref:
                equipment_nodes[n.equipment_ref].add(n.id)
            for binding in n.bindings:
                point_nodes[binding.point_ref].add(n.id)
            if n.kind == "connection":
                for ref in (n.source_ref, n.target_ref):
                    if ref in self.nodes:
                        connections[ref].add(n.id)

        def neighbors(nid):
            n = self.nodes[nid]
            related = children[nid] | equipment_nodes[n.equipment_ref] | connections[nid]
            related |= {r for r in (n.parent_id, n.source_ref, n.target_ref) if r in self.nodes}
            return related - {nid}

        is_node = object_id in self.nodes
        start = (
            {object_id}
            if is_node
            else equipment_nodes[object_id]
            if object_id in self.equipment
            else point_nodes[object_id]
        )
        direct = neighbors(object_id) if is_node else start
        # Breadth-first selection retains the requested node even if its ID sorts last.
        pending = deque((nid, 0) for nid in sorted(start))
        discovered, selected = set(start), []
        while pending and len(selected) < limit:
            nid, distance = pending.popleft()
            selected.append(nid)
            if distance < depth:
                for adjacent in sorted(neighbors(nid) - discovered):
                    pending.append((adjacent, distance + 1))
                    discovered.add(adjacent)
        equipment_ids = {self.nodes[n].equipment_ref for n in selected} - {None}
        if object_id in self.equipment:
            equipment_ids.add(object_id)
        if object_id in self.points and target.equipment_ref:
            equipment_ids.add(target.equipment_ref)
        bound = {b.point_ref for n in selected for b in self.nodes[n].bindings}
        if object_id in self.points:
            bound.add(object_id)
        available = {p.id for p in self.points.values() if p.equipment_ref in equipment_ids}
        # Bound points take priority over unrelated points from the same equipment.
        point_ids = sorted(bound) + sorted(available - bound)
        clipped = bool(pending) or len(point_ids) > limit or len(equipment_ids) > limit

        def brief(obj):
            nonlocal clipped
            if obj is None:
                return None
            value = dump(obj)
            # Preserve geometry/identity, omit opaque XML and image bytes from agent context.
            for key in ("payload", "image_uri", "metadata", "style"):
                if key in value:
                    original = value.pop(key)
                    if original:
                        value[key + "_omitted"] = True
                        clipped = True
            if "bindings" in value:
                if len(value["bindings"]) > limit:
                    clipped = True
                value["bindings"] = value["bindings"][:limit]
                for b in value["bindings"]:
                    if b.pop("metadata", None):
                        clipped = True
                        b["metadata_omitted"] = True

            # Arbitrary labels/expressions can be very large. Bound all strings, recursively.
            def shorten(v):
                nonlocal clipped
                if isinstance(v, str) and len(v) > 512:
                    clipped = True
                    return v[:512] + "…"
                if isinstance(v, dict):
                    return {k: shorten(x) for k, x in v.items()}
                if isinstance(v, list):
                    return [shorten(x) for x in v]
                return v

            return shorten(value)

        parent = (
            self.nodes.get(target.parent_id)
            if is_node
            else (self.equipment.get(target.parent) if object_id in self.equipment else None)
        )
        connected = set()
        for nid in selected:
            if self.nodes[nid].kind == "connection":
                connected.update(
                    r
                    for r in (self.nodes[nid].source_ref, self.nodes[nid].target_ref)
                    if r in selected and r != object_id
                )
        node_values = [brief(self.nodes[n]) for n in selected]
        result = {
            "target": brief(target),
            "parent": brief(parent),
            "depth": depth,
            "limit": limit,
            "children": sorted(children[object_id] & set(selected)),
            "nodes": node_values,
            "directly_related_nodes": sorted(direct & set(selected)),
            "connected_nodes": sorted(connected),
            "connections": [n for n in selected if self.nodes[n].kind == "connection"],
            "equipment": [brief(self.equipment[e]) for e in sorted(equipment_ids)[:limit]],
            "points": [brief(self.points[p]) for p in point_ids[:limit]],
            "bindings": {n["id"]: n["bindings"] for n in node_values if n["bindings"]},
        }
        result["truncated"] = clipped
        return result
