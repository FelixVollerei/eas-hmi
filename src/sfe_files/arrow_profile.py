"""Typed edits to Arrow IPC file variant with record-batch boundaries preserved."""

from __future__ import annotations

import pyarrow as pa
from pyarrow import ipc

from . import columnar_profile as common
from .plan_ops import FileEditError

OPS = common.OPS

CONTRACT = {
    "addressing": "Inspect /rows/1 or /by-key/id/P-01; plans select a unique string key column.",
    "operations": {
        "set-field": {"example": {"op": "set-field", "key_column": "id", "key": "P-01", "column": "status", "value": "ready"}},
        "append-row": {"example": {"op": "append-row", "key_column": "id", "row": {"id": "T-03", "status": "pending"}}},
    },
    "limitations": [
        "Arrow IPC file variant only; stream variant is rejected.",
        "Scalar string, boolean, integer and float columns only; no nested or dictionary columns.",
        "At most 10000 rows and 100 columns; schema metadata and original record-batch lengths are retained.",
        "Rewrites the file; append-row creates one new record batch.",
    ],
    "reference": "https://arrow.apache.org/docs/format/Columnar.html",
}


def parse(data):
    try:
        reader = ipc.open_file(pa.BufferReader(data))
        batches = [reader.get_batch(i) for i in range(reader.num_record_batches)]
        table = pa.Table.from_batches(batches, schema=reader.schema)
        return common.make_document(data, table, [batch.num_rows for batch in batches], "arrow")
    except (pa.ArrowException, OSError, ValueError) as exc:
        raise FileEditError("INVALID_ARROW", str(exc)) from exc


def serialize(document):
    if not document.dirty:
        return document.raw
    table = common.table_for_write(document)
    output = pa.BufferOutputStream()
    try:
        with ipc.new_file(output, table.schema) as writer:
            offset = 0
            for size in document.groups:
                if size:
                    writer.write_batch(table.slice(offset, size).to_batches(max_chunksize=size)[0])
                offset += size
        return output.getvalue().to_pybytes()
    except (pa.ArrowException, OSError, ValueError) as exc:
        raise FileEditError("INVALID_ARROW", str(exc)) from exc


def summary(document):
    return common.summary(document)


def inspect(document, pointer=""):
    return common.inspect(document, pointer)


def projection(document):
    return common.projection(document)


def edit(document, step):
    return common.edit(document, step)
