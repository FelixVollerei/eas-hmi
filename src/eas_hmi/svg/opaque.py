"""Retain SVG artwork; namespace references and scope a conservative CSS subset.

The canonical payload is untouched by rendering. Unsupported CSS is rejected
explicitly instead of silently dropped or heuristically rewritten.
"""

from copy import deepcopy

import tinycss2
from lxml import etree

from ..errors import EngineeringError
from .common import local, safe_parse, tag


def prefix(node_id):
    return "opaque-" + node_id.encode("utf-8").hex() + "-"


def scope_id(node_id):
    return "payload-" + node_id.encode("utf-8").hex()


def remap_tokens(tokens, mapper, selectors=False):
    for token in tokens:
        if token.type == "error":
            raise EngineeringError("UNSUPPORTED_OPAQUE_CSS", "CSS could not be safely parsed")
        if token.type == "url":
            if not token.value.startswith("#"):
                raise EngineeringError("EXTERNAL_RESOURCE", "External CSS URL is unsupported")
            token.value = "#" + mapper(token.value[1:])
            token.representation = 'url("' + token.value + '")'
        elif token.type == "function":
            if token.lower_name == "url":
                values = [v for v in token.arguments if v.type not in ("whitespace", "comment")]
                if len(values) != 1 or values[0].type != "string" or not values[0].value.startswith("#"):
                    raise EngineeringError("EXTERNAL_RESOURCE", "Only fragment CSS URLs are supported")
                mapped = "#" + mapper(values[0].value[1:])
                token.arguments = tinycss2.parse_component_value_list('"' + mapped + '"')
            elif selectors:
                raise EngineeringError(
                    "UNSUPPORTED_OPAQUE_CSS", "Functional selectors require a larger preservation boundary"
                )
            else:
                remap_tokens(token.arguments, mapper)
        elif token.type == "hash" and selectors and token.is_identifier:
            token.value = mapper(token.value)
        elif token.type.endswith("block"):
            if selectors:
                raise EngineeringError(
                    "UNSUPPORTED_OPAQUE_CSS", "Attribute selectors are outside stage 2's CSS subset"
                )
            remap_tokens(token.content, mapper)
    return tokens


def stylesheet(text, mapper, scope, reverse=False):
    rules = tinycss2.parse_stylesheet(text or "", skip_comments=True, skip_whitespace=True)
    output = []
    for rule in rules:
        if rule.type != "qualified-rule":
            raise EngineeringError(
                "UNSUPPORTED_OPAQUE_CSS", "CSS at-rules are unsupported; original payload retained"
            )
        # Pseudo selectors, namespaces and escapes outside identifiers need a richer CSS adapter.
        if any(t.type == "literal" and t.value in (":", "|") for t in rule.prelude):
            raise EngineeringError("UNSUPPORTED_OPAQUE_CSS", "Pseudo/namespace selectors are unsupported")
        groups, current = [], []
        for token in rule.prelude:
            if token.type == "literal" and token.value == ",":
                groups.append(current)
                current = []
            else:
                current.append(token)
        groups.append(current)
        selectors = []
        for group in groups:
            selector = tinycss2.serialize(group).strip()
            if reverse:
                required = "#" + scope + " "
                if not selector.startswith(required):
                    raise EngineeringError(
                        "UNSUPPORTED_OPAQUE_CSS", "Opaque CSS scope was removed or changed"
                    )
                selector = selector[len(required) :]
            tokens = remap_tokens(tinycss2.parse_component_value_list(selector), mapper, selectors=True)
            normalized = tinycss2.serialize(tokens).strip()
            if not normalized:
                raise EngineeringError("UNSUPPORTED_OPAQUE_CSS", "Empty CSS selector")
            selectors.append(normalized if reverse else "#" + scope + " " + normalized)
        content = tinycss2.serialize(remap_tokens(rule.content, mapper))
        output.append(",".join(selectors) + "{" + content + "}")
    return "\n".join(output)


def transform_tree(root, node_id, reverse=False):
    root = deepcopy(root)
    pre = prefix(node_id)
    ids = [e.get("id") for e in root.iter() if isinstance(e.tag, str) and e.get("id")]
    if len(ids) != len(set(ids)):
        raise EngineeringError("DUPLICATE_OPAQUE_ID", "Duplicate IDs inside one opaque payload")

    def mapped(value):
        if reverse:
            if value.startswith(pre):
                try:
                    return bytes.fromhex(value[len(pre) :]).decode("utf-8")
                except (ValueError, UnicodeDecodeError) as exc:
                    raise EngineeringError(
                        "INVALID_OPAQUE_ID", "Opaque internal ID encoding was damaged"
                    ) from exc
            return value  # A newly drawn human object may have a new, ordinary ID.
        return pre + value.encode("utf-8").hex()

    for e in root.iter():
        if not isinstance(e.tag, str):
            continue
        if e.get("data-eas-id"):
            raise EngineeringError(
                "NESTED_SEMANTIC_PAYLOAD", "Opaque payload cannot contain engineering node identities"
            )
        for attr, value in list(e.attrib.items()):
            key = etree.QName(attr).localname
            if key == "id":
                e.set(attr, mapped(value))
            elif key == "href" and value.startswith("#"):
                e.set(attr, "#" + mapped(value[1:]))
            elif key in (
                "style",
                "fill",
                "stroke",
                "clip-path",
                "mask",
                "filter",
                "marker",
                "marker-start",
                "marker-mid",
                "marker-end",
                "cursor",
            ):
                tokens = tinycss2.parse_component_value_list(value)
                e.set(attr, tinycss2.serialize(remap_tokens(tokens, mapped)))
        if local(e) == "style":
            e.text = stylesheet(e.text, mapped, scope_id(node_id), reverse)
    return root


def render_payload(payload, node_id):
    if not payload:
        raise EngineeringError("MISSING_OPAQUE_PAYLOAD", f"Opaque node has no payload: {node_id}")
    content = transform_tree(safe_parse(payload), node_id)
    for index, element in enumerate(content.iter()):
        if isinstance(element.tag, str):
            if not element.get("id"):
                element.set("id", f"auto-{node_id.encode().hex()}-{index}")
            if element.tag == tag("svg") and not element.get("version"):
                element.set("version", "1.1")
    wrapper = etree.Element(tag("g"), {"id": scope_id(node_id), "data-eas-part": "opaque"})
    wrapper.append(content)
    return wrapper


def restore_payload(wrapper, node_id):
    if (
        wrapper.tag != tag("g")
        or wrapper.attrib != {"id": scope_id(node_id), "data-eas-part": "opaque"}
        or len(wrapper) != 1
    ):
        raise EngineeringError("UNSUPPORTED_OPAQUE_EDIT", "Opaque container was changed")
    # Actual artwork edits may reference editor-generated IDs; retain those.
    # Unedited payloads never take this path and retain their original bytes.
    restored = transform_tree(wrapper[0], node_id, reverse=True)
    text = etree.tostring(restored, encoding="unicode", with_tail=False)
    safe_parse(text)
    return text
