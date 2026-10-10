"""Typed edits to an ordinary Parquet file, retaining schema and row-group sizes."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

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
        "Scalar string, boolean, integer and float columns only; no nested or dictionary columns.",
        "At most 10000 rows and 100 columns. Schema/field metadata and row-group lengths are retained.",
        "Rewrites the whole file through PyArrow; encoding and compressed bytes can change.",
    ],
    "reference": "https://parquet.apache.org/docs/file-format/",
}


def parse(data):
    try:
        reader = pq.ParquetFile(pa.BufferReader(data))
        groups = [reader.metadata.row_group(i).num_rows for i in range(reader.num_row_groups)]
        table = reader.read()
        return common.make_document(data, table, groups, "parquet")
    except (pa.ArrowException, OSError, ValueError) as exc:
        raise FileEditError("INVALID_PARQUET", str(exc)) from exc


def serialize(document):
    if not document.dirty:
        return document.raw
    table = common.table_for_write(document)
    output = pa.BufferOutputStream()
    try:
        with pq.ParquetWriter(output, table.schema, compression="snappy") as writer:
            offset = 0
            for size in document.groups:
                if size:
                    writer.write_table(table.slice(offset, size), row_group_size=size)
                offset += size
        return output.getvalue().to_pybytes()
    except (pa.ArrowException, OSError, ValueError) as exc:
        raise FileEditError("INVALID_PARQUET", str(exc)) from exc


def summary(document):
    return common.summary(document)


def inspect(document, pointer=""):
    return common.inspect(document, pointer)


def projection(document):
    return common.projection(document)


def edit(document, step):
    return common.edit(document, step)
