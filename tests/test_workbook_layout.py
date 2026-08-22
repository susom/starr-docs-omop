"""Workbook column formatting follows the registry, not column position.

Widths and the wrapping format used to be a positional list per sheet, with
"the last column is always Description" baked into the wrap. Inserting a
column shifted every width onto its neighbour, and adding one after
Description moved the wrap onto it and turned every description into a single
unreadable line — silently, in both cases. These tests pin the lookup to the
column *name*.

Run with pytest, or directly::

    python tests/test_workbook_layout.py
"""

import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import generate_exports as ge  # noqa: E402

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# xlsxwriter pads every requested width by this much on the way out.
PADDING = 0.7109375

DEFAULT_WIDTH = 20


def _write(tmp_path, columns):
    """Write one sheet through the real code path and read it back."""
    frame = pd.DataFrame([{name: f"value for {name}" for name in columns}])
    path = tmp_path / "book.xlsx"
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        formats = ge.DataDictionaryExporter._formats(writer.book)
        ge.DataDictionaryExporter._write_frame(writer, formats, frame, "Sheet")
    return _read(path)


def _read(path):
    """column name -> (width, wraps) as the file actually records it."""
    book = zipfile.ZipFile(path)
    styles = ET.fromstring(book.read("xl/styles.xml"))
    wrap_by_style = []
    for xf in styles.find(f"{NS}cellXfs"):
        alignment = xf.find(f"{NS}alignment")
        wrap_by_style.append(
            alignment is not None and alignment.get("wrapText") in ("1", "true")
        )

    strings = [
        (node.find(f"{NS}t").text if node.find(f"{NS}t") is not None else "")
        for node in ET.fromstring(book.read("xl/sharedStrings.xml"))
    ]

    sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
    widths, wraps = {}, {}
    cols = sheet.find(f"{NS}cols")
    for col in cols if cols is not None else []:
        for index in range(int(col.get("min")), int(col.get("max")) + 1):
            widths[index] = float(col.get("width"))
            wraps[index] = wrap_by_style[int(col.get("style") or 0)]

    out = {}
    for cell in list(sheet.find(f"{NS}sheetData"))[0]:
        value = cell.find(f"{NS}v")
        if value is None:
            continue
        index = 0
        for letter in re.match(r"([A-Z]+)", cell.get("r")).group(1):
            index = index * 26 + (ord(letter) - 64)
        header = strings[int(value.text)] if cell.get("t") == "s" else value.text
        out[header] = (widths.get(index), wraps.get(index, False))
    return out


def test_each_column_gets_its_registry_width_and_wrap(tmp_path):
    written = _write(tmp_path, ge.ALL_FIELDS_COLUMNS)
    assert set(written) == set(ge.ALL_FIELDS_COLUMNS)
    for name, (width, wraps) in written.items():
        spec = ge.column_spec(name)
        assert width == spec["xlsx"] + PADDING, name
        assert wraps is bool(spec.get("wrap")), name


def test_reordering_the_columns_moves_their_formatting_with_them(tmp_path):
    """The Description column keeps the wrap wherever it is put."""
    reordered = list(reversed(ge.ALL_FIELDS_COLUMNS))
    assert reordered[-1] != "Description", "the point is that it is no longer last"
    written = _write(tmp_path, reordered)
    for name, (width, wraps) in written.items():
        spec = ge.column_spec(name)
        assert width == spec["xlsx"] + PADDING, name
        assert wraps is bool(spec.get("wrap")), name


def test_a_column_added_after_description_does_not_inherit_the_wrap(tmp_path):
    """The exact regression: appending a column used to steal the wrap."""
    written = _write(tmp_path, ge.ALL_FIELDS_COLUMNS + ["Not In The Registry"])
    assert written["Description"][1] is True
    assert written["Not In The Registry"] == (DEFAULT_WIDTH + PADDING, False)


if __name__ == "__main__":
    import tempfile

    failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        with tempfile.TemporaryDirectory() as directory:
            try:
                fn(Path(directory))
            except AssertionError as error:
                failed += 1
                print(f"FAIL  {name}\n      {error}")
            else:
                print(f"PASS  {name}")
    print(f"\n{failed} failed")
    sys.exit(1 if failed else 0)
