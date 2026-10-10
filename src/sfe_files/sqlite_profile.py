"""Transactional row edits on a standalone SQLite database snapshot."""

from __future__ import annotations

import base64
import sqlite3
from dataclasses import dataclass

from .plan_ops import FileEditError, _fields, _pointer_parts

OPS = {"set-cell", "append-row", "delete-row"}
CONTRACT = {
    "addressing": "Inspect /tables/<table> or /tables/<table>/rows/<primary-key>; one-column PRIMARY KEY only.",
    "operations": {
        "set-cell": {"example": {"op": "set-cell", "table": "equipment", "key": "P-01", "column": "status", "value": "ready"}},
        "append-row": {"example": {"op": "append-row", "table": "equipment", "row": {"id": "T-03", "status": "pending"}}},
        "delete-row": {"example": {"op": "delete-row", "table": "equipment", "key": "T-03"}},
    },
    "limitations": [
        "Standalone SQLite main-file snapshots only. WAL-mode databases are rejected because main-file hash omits WAL commits.",
        "At most 20 ordinary tables and 10000 rows per table; writes need a one-column PRIMARY KEY.",
        "Rows are edited in an in-memory SQLite transaction with foreign keys enabled; output is a new database file.",
        "Triggers run as SQLite defines them. BLOB values are readable as base64 but cannot be supplied in edit plans.",
    ],
    "reference": "https://www.sqlite.org/fileformat.html",
}


@dataclass
class Document:
    raw: bytes
    connection: sqlite3.Connection
    dirty: bool = False


def _bad(message, code="INVALID_SQLITE"):
    raise FileEditError(code, message)


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _tables(connection):
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _columns(connection, table):
    if table not in _tables(connection):
        _bad("Table not found", "TARGET_NOT_FOUND")
    return connection.execute(f"PRAGMA table_info({_quote(table)})").fetchall()


def _primary_key(connection, table):
    columns = _columns(connection, table)
    keys = [row[1] for row in columns if row[5]]
    if len(keys) != 1:
        _bad("Edits require exactly one PRIMARY KEY column", "UNSUPPORTED_SQLITE")
    return keys[0]


def _json_value(value):
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    return value


def _scalar(value):
    if value is not None and type(value) not in {str, int, float, bool}:
        _bad("Plan values must be JSON scalar types", "INVALID_STEP")
    return value


def parse(data):
    if len(data) < 100 or not data.startswith(b"SQLite format 3\x00"):
        _bad("Expected a SQLite 3 main database file")
    if data[18:20] == b"\x02\x02":
        _bad("WAL-mode databases need a coherent snapshot; convert to rollback journal mode first", "UNSUPPORTED_SQLITE")
    try:
        connection = sqlite3.connect(":memory:")
        connection.deserialize(data)
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            _bad("SQLite quick_check failed")
        tables = _tables(connection)
        if len(tables) > 20 or any(connection.execute(
            f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0] > 10000 for table in tables):
            _bad("Database exceeds profile size limits", "UNSUPPORTED_SQLITE")
        return Document(data, connection)
    except sqlite3.DatabaseError as exc:
        raise FileEditError("INVALID_SQLITE", str(exc)) from exc


def _rows(connection, table):
    key = _primary_key(connection, table)
    cursor = connection.execute(f"SELECT * FROM {_quote(table)} ORDER BY {_quote(key)}")
    names = [column[0] for column in cursor.description]
    return [{name: _json_value(value) for name, value in zip(names, row, strict=True)} for row in cursor]


def summary(document):
    connection = document.connection
    return {"tables": [{"name": table, "rows": connection.execute(
        f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0]} for table in _tables(connection)]}


def inspect(document, pointer=""):
    parts = _pointer_parts(pointer)
    if not parts:
        return {"pointer": pointer, "value": summary(document)}
    if len(parts) == 2 and parts[0] == "tables":
        return {"pointer": pointer, "value": {"schema": [tuple(x) for x in _columns(
            document.connection, parts[1])], "rows": _rows(document.connection, parts[1])}}
    if len(parts) == 4 and parts[0] == "tables" and parts[2] == "rows":
        rows = _rows(document.connection, parts[1])
        key = _primary_key(document.connection, parts[1])
        found = [row for row in rows if str(row[key]) == parts[3]]
        if len(found) != 1:
            _bad("Row key not found", "TARGET_NOT_FOUND")
        return {"pointer": pointer, "value": found[0]}
    _bad("Use /tables/<name> or /tables/<name>/rows/<key>", "INVALID_INSPECT")


def projection(document):
    connection = document.connection
    return {"tables": {table: {"schema": [tuple(x) for x in _columns(connection, table)],
                               "rows": _rows(connection, table)} for table in _tables(connection)}}


def edit(document, step):
    op = step["op"]
    fields = {"set-cell": {"op", "table", "key", "column", "value"},
              "append-row": {"op", "table", "row"},
              "delete-row": {"op", "table", "key"}}[op]
    _fields(step, fields, fields)
    connection = document.connection
    table = step["table"]
    if not isinstance(table, str):
        _bad("table must be a string", "INVALID_STEP")
    columns = [row[1] for row in _columns(connection, table)]
    key_column = _primary_key(connection, table)
    try:
        if op == "append-row":
            row = step["row"]
            if not isinstance(row, dict) or key_column not in row or not row.keys() <= set(columns):
                _bad("row needs the primary key and only existing columns", "INVALID_STEP")
            for value in row.values():
                _scalar(value)
            names = list(row)
            placeholders = ",".join("?" for _ in names)
            quoted = ",".join(_quote(name) for name in names)
            connection.execute(f"INSERT INTO {_quote(table)} ({quoted}) VALUES ({placeholders})",
                               [row[name] for name in names])
        else:
            key = _scalar(step["key"])
            present = connection.execute(f"SELECT 1 FROM {_quote(table)} WHERE {_quote(key_column)}=?", (key,)).fetchone()
            if present is None:
                _bad("Primary key not found", "TARGET_NOT_FOUND")
            if op == "set-cell":
                column = step["column"]
                if column not in columns or column == key_column:
                    _bad("Column missing or primary key is immutable", "INVALID_STEP")
                connection.execute(f"UPDATE {_quote(table)} SET {_quote(column)}=? WHERE {_quote(key_column)}=?",
                                   (_scalar(step["value"]), key))
            else:
                connection.execute(f"DELETE FROM {_quote(table)} WHERE {_quote(key_column)}=?", (key,))
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            _bad("Edit violates a foreign key", "INVALID_STEP")
        connection.commit()
    except sqlite3.DatabaseError as exc:
        raise FileEditError("INVALID_STEP", str(exc)) from exc
    document.dirty = True
    return document


def serialize(document):
    if not document.dirty:
        return document.raw
    if document.connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        _bad("Output SQLite quick_check failed")
    return document.connection.serialize()
