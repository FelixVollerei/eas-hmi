"""Shared typed row operations for bounded Parquet and Arrow IPC file profiles."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pyarrow as pa

from .plan_ops import FileEditError, _fields, _pointer_parts

OPS = {"set-field", "append-row"}


@dataclass
class Document:
    raw: bytes
    table: pa.Table
    groups: list[int]
    rows: list[dict]
    kind: str
    dirty: bool = False


def _bad(message, code="INVALID_COLUMNAR"):
    raise FileEditError(code, message)


def make_document(raw, table, groups, kind):
    if table.num_rows > 10000 or table.num_columns > 100:
        _bad("Table exceeds 10000 rows or 100 columns", "UNSUPPORTED_COLUMNAR")
    names = table.column_names
    if len(names) != len(set(names)) or not names:
        _bad("Column names must be unique and nonempty")
    for field in table.schema:
        if not (pa.types.is_string(field.type) or pa.types.is_boolean(field.type)
                or pa.types.is_integer(field.type) or pa.types.is_floating(field.type)):
            _bad(f"Unsupported nested or non-scalar column type: {field.name}", "UNSUPPORTED_COLUMNAR")
    return Document(raw, table, groups, table.to_pylist(), kind)


def summary(document):
    return {"rows": len(document.rows), "columns": document.table.column_names,
            "groups": len(document.groups), "schema": str(document.table.schema)}


def _row(document, key_column, key):
    if key_column not in document.table.column_names or not isinstance(key, str):
        _bad("key_column must exist and key must be a string", "INVALID_STEP")
    field = document.table.schema.field(key_column)
    if not pa.types.is_string(field.type):
        _bad("Business key column must be a string", "UNSUPPORTED_COLUMNAR")
    found = [i for i, row in enumerate(document.rows) if row[key_column] == key]
    if len(found) != 1:
        _bad("Key must identify exactly one row", "TARGET_NOT_UNIQUE")
    return found[0]


def inspect(document, pointer=""):
    parts = _pointer_parts(pointer)
    if not parts:
        return {"pointer": pointer, "value": summary(document)}
    if len(parts) == 2 and parts[0] == "rows" and parts[1].isdigit():
        index = int(parts[1]) - 1
        if not 0 <= index < len(document.rows):
            _bad("Row index out of range", "TARGET_NOT_FOUND")
    elif len(parts) == 3 and parts[0] == "by-key":
        index = _row(document, parts[1], parts[2])
    else:
        _bad("Use /rows/<1-based-index> or /by-key/<column>/<value>", "INVALID_INSPECT")
    return {"pointer": pointer, "value": document.rows[index]}


def projection(document):
    return {"schema": [(field.name, str(field.type), field.nullable) for field in document.table.schema],
            "metadata": {k.decode("utf-8", "replace"): v.decode("utf-8", "replace")
                         for k, v in (document.table.schema.metadata or {}).items()},
            "groups": document.groups, "rows": document.rows}


def _coerce(value, field):
    if value is None:
        if not field.nullable:
            _bad("Nonnullable column cannot be null", "INVALID_STEP")
        return None
    kind = field.type
    if pa.types.is_string(kind):
        valid = isinstance(value, str)
    elif pa.types.is_boolean(kind):
        valid = type(value) is bool
    elif pa.types.is_integer(kind):
        valid = type(value) is int
    else:
        valid = type(value) in {int, float} and math.isfinite(value)
    if not valid:
        _bad(f"Value type does not match {field.name}: {kind}", "INVALID_STEP")
    try:
        pa.scalar(value, type=kind)
    except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError) as exc:
        raise FileEditError("INVALID_STEP", f"Value out of range for {field.name}") from exc
    return value


def edit(document, step):
    op = step["op"]
    fields = ({"op", "key_column", "key", "column", "value"} if op == "set-field"
              else {"op", "key_column", "row"})
    _fields(step, fields, fields)
    key_column = step["key_column"]
    if not isinstance(key_column, str) or key_column not in document.table.column_names:
        _bad("key_column must name an existing column", "INVALID_STEP")
    if op == "set-field":
        index = _row(document, key_column, step["key"])
        column = step["column"]
        if not isinstance(column, str) or column not in document.table.column_names or column == key_column:
            _bad("column must exist and cannot be the business key", "INVALID_STEP")
        document.rows[index][column] = _coerce(step["value"], document.table.schema.field(column))
    else:
        row = step["row"]
        if not isinstance(row, dict) or set(row) != set(document.table.column_names):
            _bad("append-row needs every schema column exactly once", "INVALID_STEP")
        key = row[key_column]
        if not isinstance(key, str) or any(old[key_column] == key for old in document.rows):
            _bad("Business key must be a new string", "INVALID_STEP")
        if len(document.rows) >= 10000:
            _bad("Table would exceed 10000 rows", "UNSUPPORTED_COLUMNAR")
        result = {field.name: _coerce(row[field.name], field) for field in document.table.schema}
        document.rows.append(result)
        document.groups.append(1)
    document.dirty = True
    return document


def table_for_write(document):
    try:
        return pa.Table.from_pylist(document.rows, schema=document.table.schema)
    except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
        raise FileEditError("INVALID_STEP", f"Cannot build typed output: {exc}") from exc
