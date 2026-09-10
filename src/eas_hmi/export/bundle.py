"""Prepare every selected asset before publishing an immutable export directory."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from ..errors import EngineeringError
from ..model.core import canonical_json
from ..operations.transaction import atomic_write
from ..query import Query
from .assets import export_asset


def asset_selection(project, node_id=None, template=None, page=None):
    if sum(value is not None for value in (node_id, template, page)) > 1:
        raise EngineeringError("INVALID_ASSET_SELECTION", "Choose one of --id, --template, --page")
    if node_id is not None:
        node = Query(project).node(node_id)
        return [(node.page_id, node.id)]
    if template is not None:
        if template not in {t.id for t in project.templates}:
            raise EngineeringError("TEMPLATE_NOT_FOUND", f"Unknown template {template}")
        result = [(n.page_id, n.id) for n in Query(project).select(template=template)]
        if not result:
            raise EngineeringError("INVALID_ASSET_SELECTION", "Template has no instances")
        return result
    pages = [p for p in project.pages if page is None or p.id == page]
    if not pages:
        raise EngineeringError("PAGE_NOT_FOUND", "No pages match the selection")
    return [(p.id, None) for p in sorted(pages, key=lambda p: p.id)]


def export_bundle(
    project,
    directory,
    formats=("svg", "png", "bmp"),
    node_id=None,
    template=None,
    page=None,
    width=None,
    height=None,
    background="#ffffff",
    backend=None,
):
    selection = asset_selection(project, node_id, template, page)
    if not formats or len(formats) != len(set(formats)) or set(formats) - {"svg", "png", "bmp"}:
        raise EngineeringError("INVALID_ASSET_FORMAT", "Choose unique formats from svg,png,bmp")
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    run_id = f"r{project.revision}-{uuid4().hex}"
    final = directory / run_id
    result = {"ok": True, "revision": project.revision, "directory": str(final), "assets": []}
    with TemporaryDirectory(prefix=".assets-", dir=directory) as temporary:
        staging = Path(temporary)
        for page_id, node in selection:
            stem = ("node-" + node.encode().hex()) if node is not None else ("page-" + page_id.encode().hex())
            for format in formats:
                filename = stem + "." + format
                item = export_asset(
                    project, page_id, staging / filename, node, width, height, background, backend
                )
                item.update(path=str(final / filename), page=page_id, node_id=node)
                result["assets"].append(item)
        atomic_write(staging / "manifest.json", canonical_json(result))
        os.replace(staging, final)
    # A failed conversion cannot leave a partly published bundle.
    return result
