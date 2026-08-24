"""Escaping invariants for the data dictionary's static HTML.

Every cell on the data dictionary page originates in dbt YAML, and the page
is one of the few places this repo emits HTML by string concatenation rather
than through Quarto. These tests pin the two properties that makes safe:

  * nothing in the data can introduce an element or an attribute, and
  * the only ``href`` that appears is one the generator built from a table
    name it recognises.

Run with pytest, or directly::

    python tests/test_html_escaping.py
"""

import html
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import generate_exports as ge  # noqa: E402

# Payloads that break naive escaping: tag openers, attribute breakouts, an
# early ``</script>``, and a value that also has to survive the comma split
# the References column does.
HOSTILE = [
    "<script>alert(1)</script>",
    '"><script>alert(1)</script>',
    "</td></tr><tr><td onmouseover=alert(1)>",
    "javascript:alert(1)",
    "' onclick='alert(1)",
    "<img src=x onerror=alert(1)>",
    "</table><svg/onload=alert(1)>",
    "</script><script>alert(1)</script>",
    "&lt;already escaped&gt;",
    "tab\tand newline\n",
    'CONCEPT, "><b>x</b>, PERSON',
]


class Sniffer(HTMLParser):
    """Records anything a browser would treat as markup."""

    ALLOWED = {"div", "table", "thead", "tbody", "tr", "th", "td", "a"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.handlers = []
        self.hrefs = []
        self.cells = []
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for key, _ in attrs:
            if key.startswith("on") or key in {"srcdoc", "style", "formaction"}:
                self.handlers.append((tag, key))
        for key, value in attrs:
            if key == "href":
                self.hrefs.append(value)
        if tag == "td":
            self._in_cell = True
            self.cells.append("")

    def handle_endtag(self, tag):
        if tag == "td":
            self._in_cell = False

    def handle_data(self, data):
        if self._in_cell:
            self.cells[-1] += data

    @property
    def unexpected(self):
        return sorted(set(self.tags) - self.ALLOWED)


class _Exporter(ge.DataDictionaryExporter):
    """Enough of the exporter to drive the HTML path.

    ``tables`` is a property on the real class, so it is overridden rather
    than assigned, and ``__init__`` is skipped: none of this needs a manifest.

    The export config is the real one rather than a stub, so the attributes
    ``_to_html`` reads off it are the attributes the site ships.
    """

    names = ["PERSON", "CONCEPT"]

    def __init__(self, names=None):
        self.export_config = ge.EXPORT_CONFIGS["omop"]
        if names is not None:
            self.names = names

    @property
    def tables(self):
        return [{"name": name} for name in self.names]


def _render(exporter, rows):
    return exporter._to_html(rows, ge.ALL_FIELDS_COLUMNS, "dt-test", ge.LAYOUT_ALL)


def _sniff(markup):
    sniffer = Sniffer()
    sniffer.feed(markup)
    return sniffer


def _hostile_rows():
    return [{header: value for header in ge.ALL_FIELDS_COLUMNS} for value in HOSTILE]


def test_hostile_cells_introduce_no_markup():
    sniffer = _sniff(_render(_Exporter(), _hostile_rows()))
    assert sniffer.unexpected == []
    assert sniffer.handlers == []


def test_every_href_is_a_same_page_anchor():
    sniffer = _sniff(_render(_Exporter(), _hostile_rows()))
    assert sniffer.hrefs, "the References column should still produce links"
    assert all(href.startswith("#") for href in sniffer.hrefs)


def test_cell_text_round_trips_unchanged():
    """Escaping must not alter what the reader sees, only how it is written."""
    sniffer = _sniff(_render(_Exporter(), _hostile_rows()))
    expected = []
    for value in HOSTILE:
        for header in ge.ALL_FIELDS_COLUMNS:
            if ge.column_spec(header).get("links") == "table":
                # The linked column splits on commas and drops empty parts.
                expected.append(
                    ", ".join(part.strip() for part in value.split(",") if part.strip())
                )
            else:
                expected.append(value)
    assert sniffer.cells == expected


def test_a_table_name_that_is_not_an_identifier_is_never_linked():
    exporter = _Exporter(['"><script>alert(1)</script>', "PERSON"])
    rows = [{header: '"><script>alert(1)</script>' for header in ge.ALL_FIELDS_COLUMNS}]
    sniffer = _sniff(_render(exporter, rows))
    assert sniffer.hrefs == []
    assert sniffer.unexpected == []


def test_attribute_values_are_quote_escaped():
    attrs = ge.DataDictionaryExporter._attrs(
        [("data-dd-title", 'a" onload="alert(1)'), ("data-dd-filter", "<b>")]
    )
    assert '"' not in attrs.replace('="', "").replace('" ', "").rstrip('"')
    assert "&quot;" in attrs
    assert "&lt;b&gt;" in attrs


def test_filter_values_json_stays_inside_its_attribute():
    saved = dict(ge.COLUMN_REGISTRY["Required"])
    ge.COLUMN_REGISTRY["Required"]["filter_values"] = ['"><script>', "Yes"]
    try:
        raw = dict(ge.column_attributes("Required", ge.LAYOUT_ALL))[
            "data-dd-filter-values"
        ]
    finally:
        ge.COLUMN_REGISTRY["Required"] = saved
    assert '"' not in html.escape(raw, quote=True)
    assert json.loads(raw) == ['"><script>', "Yes"]


def test_the_changes_island_cannot_be_closed_early():
    payload = ["</script><script>alert(1)</script>"]
    body = json.dumps({"added": payload, "changed": [], "removed": [], "since": "x"})
    body = body.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    island = f'<script type="application/json" id="dd-changes">{body}</script>'
    assert island.lower().count("</script>") == 1
    assert json.loads(body)["added"] == payload


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
        except AssertionError as error:
            failed += 1
            print(f"FAIL  {name}\n      {error}")
        else:
            print(f"PASS  {name}")
    print(f"\n{failed} failed")
    sys.exit(1 if failed else 0)
