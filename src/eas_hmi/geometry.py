"""Affine math shared by validation and SVG adapters. No rendering dependency."""
import math
import re

from .errors import EngineeringError

IDENTITY = (1., 0., 0., 1., 0., 0.)


def multiply(a, b):
    return (a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5])


def translation(x, y):
    return (1., 0., 0., 1., x, y)


def rotation(degrees):
    r = math.radians(degrees)
    return (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0., 0.)


def scaling(x, y):
    return (x, 0., 0., y, 0., 0.)


def node_matrix(node):
    return multiply(multiply(translation(node.x, node.y), rotation(node.rotation)),
                    scaling(node.width/node.content_width, node.height/node.content_height))


def world_matrix(node, index):
    result = node_matrix(node)
    seen = {node.id}
    parent = node.parent_id
    while parent in index:
        if parent in seen:
            raise EngineeringError("CIRCULAR_HIERARCHY", f"Cycle at {parent}")
        seen.add(parent)
        ancestor = index[parent]
        result = multiply(node_matrix(ancestor), result)
        parent = ancestor.parent_id
    return result


def bounds(node, index):
    a, b, c, d, e, f = world_matrix(node, index)
    corners = [(a*x+c*y+e, b*x+d*y+f)
               for x, y in [(0, 0), (node.content_width, 0),
                            (0, node.content_height), (node.content_width, node.content_height)]]
    return min(x for x, _ in corners), min(y for _, y in corners), max(x for x, _ in corners), max(y for _, y in corners)


def parse_transform(text):
    result = IDENTITY
    pattern = r"([A-Za-z]+)\s*\(([^)]*)\)"
    remainder = re.sub(pattern, "", text or "").strip(" ,\t\n")
    if remainder:
        raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", f"Invalid transform: {text}")
    for name, values in re.findall(pattern, text or ""):
        try:
            numbers = [float(v) for v in re.split(r"[\s,]+", values.strip()) if v]
        except ValueError as exc:
            raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", "Invalid transform numbers") from exc
        if not all(math.isfinite(v) for v in numbers):
            raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", "Non-finite transform")
        if name == "translate" and len(numbers) in (1, 2):
            part = translation(numbers[0], numbers[1] if len(numbers) == 2 else 0)
        elif name == "scale" and len(numbers) in (1, 2):
            part = scaling(numbers[0], numbers[-1])
        elif name == "rotate" and len(numbers) in (1, 3):
            part = rotation(numbers[0])
            if len(numbers) == 3:
                x, y = numbers[1:]
                part = multiply(multiply(translation(x, y), part), translation(-x, -y))
        elif name == "matrix" and len(numbers) == 6:
            part = tuple(numbers)
        else:
            raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", f"Unsupported transform: {name}")
        result = multiply(result, part)
    return result


def decompose(matrix):
    a, b, c, d, e, f = matrix
    sx, sy = math.hypot(a, b), math.hypot(c, d)
    if sx < 1e-12 or sy < 1e-12 or a*d-b*c <= 0 or abs(a*c+b*d) > 1e-7*sx*sy:
        raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", "Skew, reflection or singular matrix cannot be projected")
    return e, f, math.degrees(math.atan2(b, a)), sx, sy

