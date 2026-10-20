#!/usr/bin/env python3
"""VOParquet copies of the WorldsIndex ECSV exports (Framework v2, Track 1, part B).

The static builder writes every public table as ECSV 1.0 (scripts/worldsindex_interop.mjs,
rows already sorted by the HEALPix order-8 nested index they carry as a column). This
script reads one of those files with Astropy and writes it as Apache Parquet with the
IVOA VOParquet 1.0 metadata (file keys ``IVOA.VOTable-Parquet.version`` and
``IVOA.VOTable-Parquet.content``, the VOTable 1.5 header with every FIELD's UCD, unit
and description and no DATA), zstd-compressed, row groups of 50,000 rows, in the row
order of the ECSV. Masked numbers become Parquet nulls; strings keep "" for absent.

The files are release assets (uploaded by the publisher to the rolling GitHub Release
``worldsindex-data-latest``), never part of the GitHub Pages tree; the Pages tree
carries their names, byte lengths and SHA-256 digests in worldsindex/data/bulk-downloads.json.

Usage:
  worldsindex_parquet.py --check                       exit 0 when astropy and pyarrow import
  worldsindex_parquet.py --ecsv exports/objects.ecsv.gz --output dir/worldsindex-objects.parquet --name objects
Prints one JSON line: {"name", "path", "bytes", "sha256", "rows", "columns"}.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import sys
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

VOPARQUET_VERSION = "1.0"
VOPARQUET_VERSION_KEY = b"IVOA.VOTable-Parquet.version"
VOPARQUET_CONTENT_KEY = b"IVOA.VOTable-Parquet.content"
VOTABLE_NAMESPACE = "http://www.ivoa.net/xml/VOTable/v1.3"
VOTABLE_VERSION = "1.5"
ROW_GROUP_ROWS = 50_000
INTEROP_CLAIM = (
    "Formats and metadata follow the IVOA documents named in each file; the content is the same "
    "retained public catalog projection as the JSON release, not an independent measurement."
)


def check() -> int:
    try:
        import astropy  # noqa: F401
        import pyarrow  # noqa: F401
    except Exception as error:  # pragma: no cover - environment dependent
        print(f"unavailable: {type(error).__name__}: {error}")
        return 1
    print("ok")
    return 0


def read_ecsv(path: Path):
    """The ECSV file as (header table, arrow table).

    The YAML header (datatype, unit, UCD, description per column) is parsed by Astropy on
    the header alone; the rows are read by pyarrow's CSV reader with the header's column
    types, which is twenty times faster than Astropy's reader on the 460,000-row
    measurements table and gives the same cells: empty numbers and booleans are null,
    empty strings stay "".
    """

    import pyarrow as pa
    import pyarrow.csv as pacsv
    from astropy.table import Table

    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    newline = raw.find(b"\n")
    header_lines = []
    position = 0
    while newline >= 0 and raw[position:position + 1] == b"#":
        header_lines.append(raw[position:newline].decode("utf-8"))
        position = newline + 1
        newline = raw.find(b"\n", position)
    column_line_end = newline if newline >= 0 else len(raw)
    column_line = raw[position:column_line_end].decode("utf-8")
    header = Table.read(header_lines + [column_line], format="ascii.ecsv")
    arrow_types = {}
    for name in header.colnames:
        kind = header[name].dtype.kind
        arrow_types[name] = {"U": pa.string(), "S": pa.string(), "O": pa.string(), "f": pa.float64(), "i": pa.int64(), "b": pa.bool_()}[kind]
    arrow = pacsv.read_csv(
        io.BytesIO(raw[position:]),
        read_options=pacsv.ReadOptions(block_size=64 * 1024 * 1024),
        parse_options=pacsv.ParseOptions(delimiter=",", quote_char='"', double_quote=True, newlines_in_values=True),
        convert_options=pacsv.ConvertOptions(column_types=arrow_types, null_values=[""], strings_can_be_null=False, true_values=["True", "true"], false_values=["False", "false"]),
    )
    if arrow.column_names != header.colnames:
        raise SystemExit("ECSV column order differs between header and data")
    return header, arrow


def header_xml(table, name: str) -> bytes:
    """The VOTable the VOParquet metadata carries: FIELDs with UCD, unit and description, no DATA."""

    from astropy.io.votable import from_table
    from astropy.io.votable.tree import Info

    document = from_table(table[:0])
    document.version = VOTABLE_VERSION
    document.description = INTEROP_CLAIM
    resource = document.resources[0]
    vo_table = resource.tables[0]
    vo_table.name = name
    vo_table.description = str(table.meta.get("description") or "")
    for field in vo_table.fields:
        col = table[field.name]
        if field.datatype in {"unicodeChar", "char"}:
            field.datatype = "char"
            if field.arraysize in (None, "0", "*"):
                field.arraysize = "1"
        if field.datatype == "bit":
            field.datatype = "boolean"
        ucd = (col.meta or {}).get("ucd")
        if ucd:
            field.ucd = ucd
        if col.unit is not None:
            field.unit = str(col.unit)
        if col.description:
            field.description = col.description
    vo_table.infos.append(Info(name="format", value="VOParquet 1.0 header: FIELDs of the Parquet columns, no DATA"))
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        document.to_xml(stream)
    root = ET.fromstring(stream.getvalue())
    ns = "{" + VOTABLE_NAMESPACE + "}"
    for vo in root.iter(ns + "TABLE"):
        for data in vo.findall(ns + "DATA"):
            vo.remove(data)
    ET.register_namespace("", VOTABLE_NAMESPACE)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_parquet(header, arrow, name: str, output: Path) -> dict:
    import pyarrow.parquet as pq

    arrow = arrow.replace_schema_metadata({
        VOPARQUET_VERSION_KEY: VOPARQUET_VERSION.encode(),
        VOPARQUET_CONTENT_KEY: header_xml(header, name),
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    pq.write_table(arrow, temporary, row_group_size=ROW_GROUP_ROWS, compression="zstd")
    temporary.replace(output)
    raw = output.read_bytes()
    return {"name": output.name, "path": str(output), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "rows": arrow.num_rows, "columns": arrow.num_columns}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--ecsv")
    parser.add_argument("--output")
    parser.add_argument("--name", default="table")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    if not args.ecsv or not args.output:
        parser.error("--ecsv and --output are required")
    header, arrow = read_ecsv(Path(args.ecsv))
    result = write_parquet(header, arrow, args.name, Path(args.output))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
