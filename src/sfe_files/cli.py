"""JSON CLI for portable structured-file edit profiles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from typer.core import TyperGroup

from .plan_ops import FileEditError
from .service import apply_data, contract_data, inspect_data, preview_data, status_data


def emit(value):
    typer.echo(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


class JsonUsageGroup(TyperGroup):
    def main(self, *args, **kwargs):
        kwargs["standalone_mode"] = False
        try:
            result = super().main(*args, **kwargs)
        except Exception as exc:
            if callable(getattr(exc, "format_message", None)) and isinstance(
                getattr(exc, "exit_code", None), int
            ):
                emit({"ok": False, "code": "USAGE_ERROR", "message": exc.format_message()})
                raise SystemExit(exc.exit_code) from exc
            raise
        if isinstance(result, int):
            raise SystemExit(result)
        return result


app = typer.Typer(
    cls=JsonUsageGroup, no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False
)


def guarded(function):
    from functools import wraps

    @wraps(function)
    def run(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except FileEditError as exc:
            emit(exc.as_dict())
            raise typer.Exit(2) from exc
        except OSError as exc:
            emit({"ok": False, "code": "IO_ERROR", "message": str(exc)})
            raise typer.Exit(4) from exc

    return run


@app.command()
@guarded
def contract():
    """List format profiles and their supported edit commands."""
    emit(contract_data())


@app.command()
@guarded
def status(file: Path):
    """Read format, source fingerprint and a compact structure summary."""
    emit(status_data(file))


@app.command()
@guarded
def inspect(
    file: Path,
    pointer: str = typer.Option("", "--pointer"),
    key_column: str | None = typer.Option(None, "--key-column"),
    key: str | None = typer.Option(None, "--key"),
    heading: str | None = typer.Option(None, "--heading"),
    object_id: str | None = typer.Option(None, "--id"),
    limit: int = typer.Option(20, "--limit", min=1, max=1000),
):
    """Read a format-specific pointer, CSV row, Markdown section or SVG ID."""
    emit(
        inspect_data(
            file,
            pointer=pointer,
            key_column=key_column,
            key=key,
            heading=heading,
            object_id=object_id,
            limit=limit,
        )
    )


@app.command()
@guarded
def preview(file: Path, plan: Path, limit: int = typer.Option(200, "--limit", min=1, max=10000)):
    """Validate all steps and return a bounded diff without writing."""
    emit(preview_data(file, plan.read_bytes(), limit=limit))


@app.command()
@guarded
def apply(file: Path, plan: Path, output: Annotated[Path, typer.Option("--output")]):
    """Write edited output to a new path; preserve the input."""
    emit(apply_data(file, plan.read_bytes(), output))


def main():
    app()


if __name__ == "__main__":
    main()
