"""Local CLI for structured, auditable engineering edits and derived exports."""

import json
import time
from functools import wraps
from pathlib import Path

import typer
from filelock import Timeout
from pydantic import ValidationError
from typer.core import TyperGroup

from .errors import EngineeringError
from .export.bundle import export_bundle
from .export.manifest import manifest_files
from .model import Page, Project, canonical_hash
from .model.core import canonical_json
from .operations import edit
from .operations.transaction import Store, atomic_write, check_actor
from .query import Query
from .svg.importer import append_import, import_svg
from .svg.renderer import render as render_svg
from .svg.synchronizer import plan_sync
from .validation import validate as validate_project
from .validation import validate_raw


class JsonUsageGroup(TyperGroup):
    """Keep syntax errors machine-readable as well as domain errors."""

    def main(self, *args, **kwargs):
        kwargs["standalone_mode"] = False
        try:
            result = super().main(*args, **kwargs)
        except Exception as exc:
            # Click/Typer usage exceptions expose this public error protocol;
            # do not catch or relabel unexpected programming exceptions.
            if callable(getattr(exc, "format_message", None)) and isinstance(
                getattr(exc, "exit_code", None), int
            ):
                emit(EngineeringError("USAGE_ERROR", exc.format_message()).as_dict())
                raise SystemExit(exc.exit_code) from exc
            raise
        if isinstance(result, int):
            raise SystemExit(result)
        return result


app = typer.Typer(
    cls=JsonUsageGroup,
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    help="EAS-HMI: structured, local engineering transactions.",
)


def emit(value):
    typer.echo(canonical_json(value))


def guarded(func):
    @wraps(func)
    def run(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except EngineeringError as exc:
            emit(exc.as_dict())
            code = 3 if exc.code == "REVISION_CONFLICT" else (4 if exc.code == "STORE_CORRUPT" else 2)
            raise typer.Exit(code) from exc
        except Timeout as exc:
            emit(EngineeringError("PROJECT_BUSY", "Project lock timed out").as_dict())
            raise typer.Exit(4) from exc
        except ValidationError as exc:
            emit(
                EngineeringError(
                    "VALIDATION_FAILED",
                    "Schema validation failed",
                    exc.errors(include_url=False, include_context=False, include_input=False),
                ).as_dict()
            )
            raise typer.Exit(2) from exc
        except (OSError, UnicodeError) as exc:
            emit(EngineeringError("IO_ERROR", str(exc)).as_dict())
            raise typer.Exit(4) from exc
        except (ValueError, TypeError) as exc:
            emit(EngineeringError("INVALID_INPUT", str(exc)).as_dict())
            raise typer.Exit(2) from exc

    return run


@app.callback()
@guarded
def options(
    ctx: typer.Context,
    project: Path = typer.Option(Path("."), "--project"),
    actor: str | None = typer.Option(None, "--actor"),
    expected_revision: int | None = typer.Option(None, "--expected-revision", min=0),
    json_output: bool = typer.Option(False, "--json"),
    auto_render: bool = typer.Option(False, "--auto-render"),
):
    """Global options precede the subcommand. JSON is the default data format."""
    if actor is not None:
        check_actor(actor)
    ctx.obj = {
        "store": Store(project),
        "actor": actor,
        "expected_revision": expected_revision,
        "json": json_output,
        "auto_render": auto_render,
    }


def store(ctx):
    return ctx.obj["store"]


def actor(ctx, default="agent"):
    return ctx.obj["actor"] or default


def page_for(project, page):
    if page is None and len(project.pages) == 1:
        return project.pages[0]
    found = next((p for p in project.pages if p.id == page), None)
    if found is None:
        raise EngineeringError("PAGE_NOT_FOUND", "Specify an existing --page (required for multiple pages)")
    return found


def output_path(state, path):
    path = Path(path).resolve()
    if (
        path == state.head
        or path == state.root / ".eas.lock"
        or any(path.is_relative_to(state.root / name) for name in (".eas", "history"))
    ):
        raise EngineeringError(
            "PROTECTED_PROJECT_PATH", "Derived output cannot overwrite canonical storage/history"
        )
    return path


def finish_write(ctx, result):
    if ctx.obj["auto_render"] and result["committed"]:
        try:
            current = store(ctx).load()
            if current.revision != result["revision"]:
                raise EngineeringError("REVISION_CONFLICT", "Newer commit exists; automatic render skipped")
            # Prepare all pages first. Distinct revision names cannot overwrite a newer build.
            data = [(p.id, render_svg(current, p.id)) for p in current.pages]
            outputs = []
            for page, svg in data:
                path = store(ctx).root / "build" / f"page-{page.encode().hex()}-r{current.revision}.svg"
                atomic_write(path, svg)
                outputs.append(str(path))
            result["rendered"] = outputs
            result["rendered_revision"] = current.revision
        except (EngineeringError, OSError) as exc:
            # Engineering state is already committed. Never report a false rollback.
            result.setdefault("warnings", []).append(f"Committed; automatic render failed: {exc}")
            result["render_failed"] = True
    emit(result)


def transaction(ctx, command, mutate, default_actor="agent"):
    result = store(ctx).transaction(command, mutate, actor(ctx, default_actor), ctx.obj["expected_revision"])
    finish_write(ctx, result)


@app.command()
@guarded
def init(
    ctx: typer.Context,
    from_model: Path | None = typer.Option(None, "--from-model"),
    name: str = "New synthetic project",
    json_output: bool = typer.Option(False, "--json"),
):
    """Initialize an empty page or a validated canonical fixture; never overwrite a project."""
    if ctx.obj["expected_revision"] is not None:
        raise EngineeringError("INVALID_INPUT", "--expected-revision applies to existing projects")
    project = (
        Project.model_validate_json(from_model.read_text(encoding="utf-8"))
        if from_model
        else Project(id="NEW_PROJECT", name=name, pages=[Page(id="main", name="Main")])
    )
    result = store(ctx).initialize(project, actor(ctx, "system"), f"init from-model={from_model}")
    finish_write(ctx, result)


@app.command()
@guarded
def status(ctx: typer.Context, json_output: bool = typer.Option(False, "--json")):
    """Read revision, semantic hash and project counts."""
    project = store(ctx).load()
    emit(
        {
            "ok": True,
            "project_id": project.id,
            "revision": project.revision,
            "canonical_hash": canonical_hash(project),
            "pages": len(project.pages),
            "nodes": len(project.all_nodes()),
            "points": len(project.points),
        }
    )


@app.command()
@guarded
def inspect(ctx: typer.Context, object_id: str, json_output: bool = typer.Option(False, "--json")):
    emit(Query(store(ctx).load()).inspect(object_id))


@app.command()
@guarded
def query(
    ctx: typer.Context,
    kind: str | None = None,
    page: str | None = None,
    equipment: str | None = None,
    missing_binding: str | None = None,
    equipment_type: str | None = None,
    template: str | None = None,
    json_output: bool = typer.Option(False, "--json"),
):
    nodes = Query(store(ctx).load()).select(
        kind=kind,
        page=page,
        equipment=equipment,
        missing_binding=missing_binding,
        equipment_type=equipment_type,
        template=template,
    )
    emit({"count": len(nodes), "nodes": [n.model_dump(mode="json") for n in nodes]})


@app.command("set", context_settings={"ignore_unknown_options": True})
@guarded
def set_command(
    ctx: typer.Context,
    object_id: str,
    property_name: str,
    value: str,
    json_output: bool = typer.Option(False, "--json"),
):
    transaction(
        ctx,
        canonical_json(["set", object_id, property_name, value]),
        lambda p: edit.set_property(p, [object_id], property_name, edit.parse_value(value)),
    )


@app.command()
@guarded
def move(
    ctx: typer.Context,
    object_id: str,
    dx: float = 0,
    dy: float = 0,
    json_output: bool = typer.Option(False, "--json"),
):
    transaction(ctx, canonical_json(["move", object_id, dx, dy]), lambda p: edit.move(p, [object_id], dx, dy))


@app.command()
@guarded
def resize(
    ctx: typer.Context,
    object_id: str,
    width: float | None = None,
    height: float | None = None,
    json_output: bool = typer.Option(False, "--json"),
):
    transaction(
        ctx,
        canonical_json(["resize", object_id, width, height]),
        lambda p: edit.resize(p, [object_id], width, height),
    )


@app.command()
@guarded
def bind(
    ctx: typer.Context,
    object_id: str,
    role: str,
    point_ref: str,
    expression: str | None = None,
    json_output: bool = typer.Option(False, "--json"),
):
    transaction(
        ctx,
        canonical_json(["bind", object_id, role, point_ref, expression]),
        lambda p: edit.bind(p, object_id, role, point_ref, expression),
    )


@app.command()
@guarded
def unbind(ctx: typer.Context, object_id: str, role: str, json_output: bool = typer.Option(False, "--json")):
    transaction(ctx, canonical_json(["unbind", object_id, role]), lambda p: edit.unbind(p, object_id, role))


@app.command("validate")
@guarded
def validate_command(
    ctx: typer.Context,
    strict: bool = False,
    file: Path | None = None,
    json_output: bool = typer.Option(False, "--json"),
):
    issues = (
        validate_raw(json.loads(file.read_text(encoding="utf-8")), strict=strict)
        if file
        else validate_project(store(ctx).load(), strict=strict)
    )
    ok = not any(i["severity"] == "ERROR" for i in issues)
    emit({"ok": ok, "issues": issues})
    if not ok:
        raise typer.Exit(2)


@app.command()
@guarded
def history(
    ctx: typer.Context,
    limit: int = typer.Option(10, min=1, max=1000),
    json_output: bool = typer.Option(False, "--json"),
):
    emit({"operations": store(ctx).history()[-limit:]})


@app.command()
@guarded
def diff(
    ctx: typer.Context,
    revision: int | None = typer.Option(None, min=0),
    json_output: bool = typer.Option(False, "--json"),
):
    operations = store(ctx).history()
    operation = (
        operations[-1]
        if revision is None
        else next((o for o in operations if o["revision_after"] == revision), None)
    )
    if operation is None:
        raise EngineeringError("REVISION_NOT_FOUND", f"No transaction at revision {revision}")
    if json_output or ctx.obj["json"]:
        emit(operation)
    else:
        typer.echo(f"Transaction {operation['transaction_id']} · revision {operation['revision_after']}")
        for change in operation["changes"]:
            typer.echo(change["object_id"])
            for field, values in change["changes"].items():
                typer.echo(f"  {field}: {canonical_json(values['old'])} -> {canonical_json(values['new'])}")


@app.command()
@guarded
def undo(ctx: typer.Context, json_output: bool = typer.Option(False, "--json")):
    finish_write(ctx, store(ctx).undo(actor(ctx), ctx.obj["expected_revision"]))


@app.command()
@guarded
def render(
    ctx: typer.Context,
    page: str | None = None,
    output: Path | None = None,
    output_dir: Path | None = None,
    json_output: bool = typer.Option(False, "--json"),
):
    project = store(ctx).load()
    if output is not None and output_dir is not None:
        raise EngineeringError("INVALID_INPUT", "Choose --output or --output-dir")
    if page is None and len(project.pages) != 1:
        if not project.pages:
            raise EngineeringError("PAGE_NOT_FOUND", "Project has no pages")
        if output is not None:
            raise EngineeringError(
                "INVALID_INPUT", "Multiple pages require --output-dir or an explicit --page"
            )
        directory = output_dir or store(ctx).root / "build"
        prepared = [
            (
                output_path(store(ctx), directory / f"page-{p.id.encode().hex()}-r{project.revision}.svg"),
                render_svg(project, p.id),
            )
            for p in project.pages
        ]
        for path, data in prepared:
            atomic_write(path, data)
        emit(
            {
                "ok": True,
                "revision": project.revision,
                "paths": [str(p) for p, _ in prepared],
                "canonical_hash": canonical_hash(project),
            }
        )
        return
    selected = page_for(project, page)
    path = output_path(
        store(ctx),
        output
        or (output_dir or store(ctx).root / "build")
        / f"page-{selected.id.encode().hex()}-r{project.revision}.svg",
    )
    atomic_write(path, render_svg(project, selected.id))
    emit(
        {
            "ok": True,
            "revision": project.revision,
            "path": str(path),
            "canonical_hash": canonical_hash(project),
        }
    )


@app.command("sync-from-svg")
@guarded
def sync(
    ctx: typer.Context, file: Path, dry_run: bool = False, json_output: bool = typer.Option(False, "--json")
):
    data = file.read_bytes()
    if dry_run:
        current = store(ctx).load()
        if ctx.obj["expected_revision"] is not None and current.revision != ctx.obj["expected_revision"]:
            raise EngineeringError("REVISION_CONFLICT", "Revision changed before sync preview")
        plan = plan_sync(current, data)
        emit(
            {
                "ok": True,
                "committed": False,
                "dry_run": True,
                "revision": current.revision,
                "changes": plan.changes,
            }
        )
    else:
        transaction(
            ctx,
            canonical_json(["sync-from-svg", str(file.resolve())]),
            lambda p: plan_sync(p, data).apply(p),
            default_actor="human",
        )


@app.command()
@guarded
def context(
    ctx: typer.Context,
    object_id: str,
    depth: int = typer.Option(1, min=0, max=3),
    limit: int = typer.Option(30, min=1, max=200),
    json_output: bool = typer.Option(False, "--json"),
):
    """Bounded local graph; large artwork and arbitrary metadata are omitted."""
    emit(Query(store(ctx).load()).context(object_id, depth, limit))


def selection_ids(ids):
    selected = [part.strip() for part in ids.split(",")]
    if not all(selected) or len(selected) != len(set(selected)):
        raise EngineeringError("INVALID_SELECTION", "--ids requires unique, nonempty comma-separated IDs")
    return selected


@app.command()
@guarded
def align(
    ctx: typer.Context,
    ids: str = typer.Option(...),
    mode: str = typer.Option(...),
    json_output: bool = typer.Option(False, "--json"),
):
    selected = selection_ids(ids)
    transaction(ctx, canonical_json(["align", selected, mode]), lambda p: edit.align(p, selected, mode))


@app.command()
@guarded
def distribute(
    ctx: typer.Context,
    ids: str = typer.Option(...),
    axis: str = "x",
    gap: float = typer.Option(20, min=0),
    columns: int | None = typer.Option(None, min=1),
    json_output: bool = typer.Option(False, "--json"),
):
    selected = selection_ids(ids)
    transaction(
        ctx,
        canonical_json(["distribute", selected, axis, gap, columns]),
        lambda p: edit.distribute(p, selected, axis, gap, columns),
    )


@app.command()
@guarded
def batch(
    ctx: typer.Context,
    operation: str,
    kind: str | None = None,
    page: str | None = None,
    equipment_type: str | None = None,
    equipment: str | None = None,
    template: str | None = None,
    missing_binding: str | None = None,
    dx: float = 0,
    dy: float = 0,
    property_name: str | None = typer.Option(None, "--property"),
    value: str | None = None,
    json_output: bool = typer.Option(False, "--json"),
):
    """Apply move or set to an AND selection in one atomic transaction."""
    if operation not in ("move", "set"):
        raise EngineeringError("INVALID_BATCH_OPERATION", "Batch supports move and set only")
    if operation == "set" and (property_name is None or value is None):
        raise EngineeringError("INVALID_INPUT", "batch set requires --property and --value")
    if (operation == "move" and (property_name is not None or value is not None)) or (
        operation == "set" and (dx != 0 or dy != 0)
    ):
        raise EngineeringError("INVALID_INPUT", "Options do not match the selected batch operation")
    selector = dict(
        kind=kind,
        page=page,
        equipment_type=equipment_type,
        equipment=equipment,
        template=template,
        missing_binding=missing_binding,
    )
    values = dict(dx=dx, dy=dy, property_name=property_name, value=edit.parse_value(value))
    transaction(
        ctx,
        canonical_json(["batch", operation, selector, values]),
        lambda p: edit.batch(p, operation, selector, **values),
    )


@app.command("export-manifest")
@guarded
def export_manifest(
    ctx: typer.Context,
    output_dir: Path | None = None,
    json_output: bool = typer.Option(False, "--json"),
):
    project = store(ctx).load()
    files, count = manifest_files(project)
    directory = output_dir or store(ctx).root / "build"
    paths = {name: output_path(store(ctx), directory / name) for name in files}
    for name, content in files.items():
        atomic_write(paths[name], content)
    emit(
        {
            "ok": True,
            "revision": project.revision,
            "rows": count,
            "files": {name: str(path) for name, path in paths.items()},
        }
    )


@app.command("import-svg")
@guarded
def import_svg_command(
    ctx: typer.Context,
    file: Path,
    json_output: bool = typer.Option(False, "--json"),
):
    """Create a project from SVG, or append its page through an audited transaction."""
    result = import_svg(file.read_bytes())
    command = canonical_json(["import-svg", str(file.resolve())])
    state = store(ctx)
    if state.head.exists():
        outcome = state.transaction(
            command,
            lambda p: append_import(p, result.project),
            actor(ctx, "human"),
            ctx.obj["expected_revision"],
        )
    else:
        if ctx.obj["expected_revision"] is not None:
            raise EngineeringError("REVISION_CONFLICT", "Import destination does not exist")
        outcome = state.initialize(result.project, actor(ctx, "human"), command)
    outcome.setdefault("warnings", []).extend(result.warnings)
    finish_write(ctx, outcome)


@app.command()
@guarded
def events(
    ctx: typer.Context,
    since_revision: int = typer.Option(-1, min=-1),
    json_output: bool = typer.Option(False, "--json"),
):
    """Read committed events after an exclusive revision cursor; -1 includes init."""
    emit({"events": store(ctx).events_since(since_revision)})


@app.command("export-assets")
@guarded
def export_assets(
    ctx: typer.Context,
    node_id: str | None = typer.Option(None, "--id"),
    template: str | None = None,
    page: str | None = None,
    formats: str = "svg,png,bmp",
    output_dir: Path | None = None,
    width: int | None = typer.Option(None, min=1, max=16384),
    height: int | None = typer.Option(None, min=1, max=16384),
    background: str = "#ffffff",
    json_output: bool = typer.Option(False, "--json"),
):
    """Export all pages by default, or select a node/template/page; publish complete bundles."""
    project = store(ctx).load()
    directory = output_path(store(ctx), output_dir or store(ctx).root / "build" / "assets")
    emit(
        export_bundle(
            project,
            directory,
            tuple(x.strip().lower() for x in formats.split(",")),
            node_id,
            template,
            page,
            width,
            height,
            background,
        )
    )


@app.command()
@guarded
def watch(
    ctx: typer.Context,
    since_revision: int | None = typer.Option(None, min=-1),
    interval: float = typer.Option(0.5, min=0.05, max=10),
    timeout: float = typer.Option(0, min=0),
    once: bool = False,
    json_output: bool = typer.Option(False, "--json"),
):
    """Stream JSONL committed events. Default cursor is current revision; timeout=0 waits until Ctrl+C."""
    cursor = store(ctx).load().revision if since_revision is None else since_revision
    started = time.monotonic()
    while True:
        changes = store(ctx).events_since(cursor)
        for event in changes:
            emit(event)
            cursor = max(cursor, event["revision"])
        if once or (timeout and time.monotonic() - started >= timeout):
            return
        delay = min(interval, max(0, timeout - (time.monotonic() - started))) if timeout else interval
        time.sleep(delay)


if __name__ == "__main__":
    app()
