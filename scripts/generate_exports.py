#!/usr/bin/env python3
"""
Data dictionary exporter for STARR-OMOP dbt models.

Produces two artifacts from the same dbt source the rest of the site uses:

    docs/omop_data_dictionary.qmd                   searchable page
    docs/downloads/starr_omop_data_dictionary.xlsx  downloadable workbook

Both are produced by a ``pre-render`` hook in ``docs/_quarto.yml``, so they
refresh on every ``quarto render``/``preview``/``publish`` like the rest of the
generated site. The page is committed; the workbook is not, and is rebuilt by
the hook on every render.

Rewriting a file on every preview would normally leave noise in ``git status``;
it does not here because both artifacts are byte-reproducible. Every timestamp
written into them derives from the dbt source commit rather than the wall clock
(see ``_read_commit_date`` and ``workbook_created``), so re-running against
unchanged models reproduces the same bytes and only a real dbt change shows up
as a diff.

Usage:
    python scripts/generate_exports.py omop
    python scripts/generate_exports.py omop --update-baseline

Note: Activate the virtual environment before running:
    source .venv/bin/activate
"""

import argparse
import datetime as dt
import html
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import pandas as pd
except ImportError:
    print("Error: pandas not found.")
    print("Please activate the virtual environment: source .venv/bin/activate")
    print("Or install dependencies: uv sync")
    sys.exit(1)

from generate_docs import (
    MODEL_CONFIGS,
    REPO_URL,
    DocGenerator,
    markdown_prose,
    table_anchor,
    table_heading,
)


EXPORT_CONFIGS = {
    "omop": {
        "page_file": "docs/omop_data_dictionary.qmd",
        "workbook_file": "docs/downloads/starr_omop_data_dictionary.xlsx",
        "page_title": "OMOP Data Dictionary",
        "page_description": (
            "Every STARR-OMOP CDM v5.4 table and field in one searchable page, "
            "downloadable as an Excel workbook."
        ),
        "workbook_title": "STARR-OMOP CDM v5.4 Data Dictionary",
        "cdm_version": "OMOP CDM v5.4",
        "model_page": "omop_data_model.qmd",
        # Stem for the filenames the grid's CSV export offers. Reaches the
        # browser as `data-dd-csv` on each static table rather than as a
        # constant in data-dictionary.js, so the script stays identical to the
        # one starr-docs-common ships.
        "csv_prefix": "starr_omop",
    },
}

# Constraint strings emitted by DocGenerator._parse_constraints that mean
# "this field must be populated".
REQUIRED_CONSTRAINTS = ("**Primary Key**", "**Not Null**")

# dbt YMLs spell scalar types in mixed case (`INT64` and `int64` both appear,
# `float` is used where BigQuery's canonical name is `FLOAT64`). Exact,
# case-insensitive matches are canonicalised; anything else — parameterised
# types like NUMERIC(9,2), or ARRAY/STRUCT declarations — is left verbatim.
SCALAR_TYPE_ALIASES = {
    "bool": "BOOL",
    "boolean": "BOOL",
    "bignumeric": "BIGNUMERIC",
    "bytes": "BYTES",
    "date": "DATE",
    "datetime": "DATETIME",
    "float": "FLOAT64",
    "float64": "FLOAT64",
    "geography": "GEOGRAPHY",
    "int64": "INT64",
    "integer": "INT64",
    "interval": "INTERVAL",
    "json": "JSON",
    "numeric": "NUMERIC",
    "string": "STRING",
    "time": "TIME",
    "timestamp": "TIMESTAMP",
}

CORE_CATEGORY = "Core CDM"
EXTENSION_CATEGORY = "Stanford extension"

# A dbt model name that may be turned into a page anchor. Every real one
# matches; the point is that the set of link targets this file can emit is
# bounded by a pattern here rather than by what the manifest happens to hold.
ANCHOR_SAFE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# ---------------------------------------------------------------------------
# Column registry
#
# One entry per column, read by every consumer: the page's static HTML (as
# ``data-dd-*`` attributes on each ``<th>``), the grid script (which reads
# those attributes rather than keeping a second copy of them), the stylesheet
# (through the ``dd-mono`` class this puts on cells), and the Excel workbook
# (widths, which column wraps, and the About sheet's glossary).
#
# Adding a column is one entry here plus one key in ``build_rows``. Nothing
# downstream is keyed by position any more, so nothing downstream can go
# quietly wrong when the column order changes.
#
#   title          grid header text, when it differs from the registry key
#   mono           render values in the monospace face
#   identifier     freeze this column when the layout scrolls sideways
#   filter         "input" | "list" — the per-column header filter in the grid
#   filter_values  fixed option list for a "list" filter
#   groupable      offer this column in the grid's "Group by" control
#   links          "table": values are table names; link them to their section
#   numeric        parse as a number and sort numerically
#   align          cell alignment
#   clamp          long prose — clamp to two lines behind an expander
#   wrap           this is the Excel column that gets the wrapping format
#   grow           share of the grid's leftover width
#   width          grid width, px
#   min_width      grid minimum width, px
#   xlsx           Excel column width, characters
#   compact        member of the "Compact" column preset
#   hidden_in      layouts where the column starts hidden in the grid
#   glossary       one-line definition, used on the page and the About sheet
# ---------------------------------------------------------------------------
COLUMN_REGISTRY: Dict[str, Dict[str, Any]] = {
    "Table": {
        "mono": True,
        "identifier": True,
        "filter": "input",
        "groupable": True,
        "links": "table",
        "compact": True,
        "width": 200,
        "xlsx": 22,
        "glossary": "Table the field belongs to. Links to that table's fields.",
    },
    "Category": {
        "filter": "list",
        "groupable": True,
        "width": 140,
        "xlsx": 18,
        "glossary": (
            f"{CORE_CATEGORY} or {EXTENSION_CATEGORY} "
            "(underscore-prefixed tables are Stanford additions)."
        ),
    },
    "Field": {
        "mono": True,
        "identifier": True,
        "filter": "input",
        "compact": True,
        # Wide enough for every field name the dictionary contains: the longest
        # (`source_documentation_reference`, 30 characters) measures 261px in the
        # grid's monospace face, and the cell adds 10px of padding either side.
        # A field name that ellipsises is a field name the reader cannot look up.
        "width": 290,
        "xlsx": 32,
        "glossary": "Column name as defined in the dbt model.",
    },
    "Required": {
        "title": "Req",
        "filter": "list",
        "filter_values": ["", "Yes", "No"],
        "groupable": True,
        "align": "center",
        "compact": True,
        "width": 80,
        "xlsx": 10,
        "glossary": ("Yes when dbt declares the column a primary key or not null."),
    },
    "Type": {
        "mono": True,
        "filter": "list",
        "groupable": True,
        "compact": True,
        "width": 125,
        "xlsx": 14,
        "glossary": "BigQuery data type, canonicalised to a single spelling.",
    },
    "References": {
        "mono": True,
        "filter": "input",
        "links": "table",
        "width": 165,
        "xlsx": 26,
        # All Fields already shows six columns in a 1400px page; a seventh is
        # available from the Columns button rather than on by default.
        "hidden_in": ("dd-layout-all",),
        "glossary": (
            "Tables this field is declared a foreign key to, from the dbt "
            "model's constraints. Blank means no foreign key is declared — "
            "not necessarily that none exists."
        ),
    },
    "Fields": {
        "numeric": True,
        "align": "right",
        "compact": True,
        "width": 90,
        "xlsx": 8,
        "glossary": "Number of fields in the table.",
    },
    "Description": {
        "clamp": True,
        "wrap": True,
        "filter": "input",
        "grow": 5,
        "min_width": 320,
        "xlsx": 90,
        "glossary": "Description from the dbt model.",
    },
}

PAGE_COLUMNS = ["Field", "Required", "Type", "References", "Description"]
ALL_FIELDS_COLUMNS = [
    "Table",
    "Category",
    "Field",
    "Required",
    "Type",
    "References",
    "Description",
]
INDEX_COLUMNS = ["Table", "Category", "Fields", "Description"]

# Layout classes, mirrored in data-dictionary.js and styles.css.
LAYOUT_ALL = "dd-layout-all"
LAYOUT_INDEX = "dd-layout-index"
LAYOUT_FIELDS = "dd-layout-fields"

# dbt spells foreign key targets as `ref('table')`. Anything else — a literal
# name, a source() call, a cross-project reference — is left as written rather
# than guessed at, and simply will not resolve to a link.
DBT_REF = re.compile(r"ref\(\s*['\"]([A-Za-z0-9_]+)['\"]\s*\)")
FOREIGN_KEY_CONSTRAINT = re.compile(r"\*\*Foreign Key\*\*\s*→\s*`([^`]*)`")

# Baseline snapshot used to badge fields that are new or retyped since the last
# release. Committed to this repository and updated deliberately
# (`--update-baseline`), never by the pre-render hook: a snapshot that
# refreshed itself on every build would compare each render against the one
# before it and never have anything to report.
BASELINE_FILE = "data/dictionary_baseline.json"
BASELINE_SCHEMA = 1

# Stanford brand tokens, mirrored from docs/styles.css :root.
BRAND_RED = "#8C1515"
BRAND_RED_EXTRA_LIGHT = "#F7E9EB"
BRAND_BLACK = "#2E2D29"
BRAND_COOL_GREY = "#4D4F53"

# Excel forbids these in worksheet names and caps them at 31 characters.
INVALID_SHEET_CHARS = r"[]:*?/\\"
MAX_SHEET_NAME = 31

TABULATOR_VERSION = "6.5.2"
TABULATOR_CDN = f"https://cdn.jsdelivr.net/npm/tabulator-tables@{TABULATOR_VERSION}"

# Subresource integrity hashes for the pinned version above. Regenerate with:
#   curl -sfL <url> | openssl dgst -sha384 -binary | openssl base64 -A
TABULATOR_CSS_SRI = (
    "sha384-Dbp8ndtzK+EKUKuD9c6EP5uRUXy2+OEN6LXCUsVF/5zRJbY7RD2TFAakK/eR/wds"
)
TABULATOR_JS_SRI = (
    "sha384-ZlfxHB5fIn8MOAuKJe8YBMi7snQXYvhy+0b3K4rGBBY2UvrJwho2jciJ5NKt0WtC"
)

# Six visible columns of identifiers and free text do not fit the ~800px article
# column Quarto's grid reserves for body content, and `page-layout: full`
# alone does not change that: the theme bakes `minmax(500px, calc(800px - 3em))`
# into the body-content track, and the page-layout class has no CSS of its own.
# Overriding `grid.body-width` for this page is the supported way to change it
# (Quarto compiles a second theme bundle for the page, which is why this is set
# here and not site-wide). `margin-width: 0` reclaims the right-hand margin,
# and the table of contents goes with it — the Tables grid below is a better
# index of a 43-section page than a 45-entry sidebar list.
#
# This width is sized for the six columns All Fields shows by default. A
# seventh does not force a change: columns past that mark themselves
# `hidden_in` in the registry and are reached from the Columns button.
PAGE_LAYOUT_FRONT_MATTER = [
    "page-layout: full",
    "toc: false",
    "grid:",
    "  body-width: 1400px",
    "  margin-width: 0px",
]

# Emitted into the generated page's own front matter rather than into
# `format.html` in _quarto.yml, so the only page that needs a data grid is the
# only page that pays to download one.
#
# Both scripts are deferred, so they execute in order once the tables are
# parsed: Tabulator first, then the initialiser that reads the static tables
# out of the DOM and replaces them (see docs/assets/data-dictionary.js).
GRID_FRONT_MATTER = [
    *PAGE_LAYOUT_FRONT_MATTER,
    "include-in-header:",
    "  text: |",
    f'    <link rel="stylesheet" href="{TABULATOR_CDN}/dist/css/tabulator_simple.min.css"',
    f'          integrity="{TABULATOR_CSS_SRI}" crossorigin="anonymous">',
    f'    <script defer src="{TABULATOR_CDN}/dist/js/tabulator.min.js"',
    f'            integrity="{TABULATOR_JS_SRI}" crossorigin="anonymous"></script>',
    '    <script defer src="assets/data-dictionary.js"></script>',
]


def normalize_type(raw: str) -> str:
    """Canonicalise a dbt ``data_type`` string."""
    value = (raw or "").strip()
    return SCALAR_TYPE_ALIASES.get(value.lower(), value)


def flatten_description(raw: str) -> str:
    """Collapse a multi-line YAML description block into a single line."""
    return re.sub(r"\s+", " ", raw or "").strip()


def is_required(constraints: List[str]) -> str:
    """A field is Required when dbt declares it primary key or not null."""
    return "Yes" if any(c in REQUIRED_CONSTRAINTS for c in constraints) else "No"


def category_for(table_name: str) -> str:
    """Underscore-prefixed tables are Stanford additions to the CDM."""
    return EXTENSION_CATEGORY if table_name.startswith("_") else CORE_CATEGORY


def sheet_name_for(table_name: str) -> str:
    """Excel-safe worksheet name for a table."""
    name = table_name.upper()
    for char in INVALID_SHEET_CHARS:
        name = name.replace(char, "_")
    return name[:MAX_SHEET_NAME]


def references_for(constraints: List[str]) -> str:
    """Tables a column is declared a foreign key to, from its constraints.

    Only declared ``foreign_key`` constraints are reported. Many descriptions
    also say "foreign key to the CONCEPT table" in prose, and parsing that
    would raise coverage from 115 fields to roughly 226 — at the cost of
    publishing a guess in a column that otherwise carries a fact. The glossary
    entry says plainly that a blank cell means nothing was declared.
    """
    names = []
    for constraint in constraints:
        match = FOREIGN_KEY_CONSTRAINT.match(constraint)
        if not match:
            continue
        target = match.group(1)
        ref = DBT_REF.match(target)
        name = (ref.group(1) if ref else target.split("(")[0]).strip()
        if name and name.upper() not in names:
            names.append(name.upper())
    return ", ".join(names)


# ---------------------------------------------------------------------------
# Column registry accessors
# ---------------------------------------------------------------------------


def field_key(header: str) -> str:
    """Canonical grid field name for a column header.

    Mirrors ``fieldName()`` in docs/assets/data-dictionary.js. It is emitted as
    ``data-dd-key`` so the grid reads the key rather than re-deriving it, and
    the two spellings cannot drift apart.
    """
    return re.sub(r"[^a-z0-9]+", "_", header.lower())


def column_spec(header: str) -> Dict[str, Any]:
    return COLUMN_REGISTRY.get(header, {})


def column_attributes(header: str, layout: str) -> List[Tuple[str, str]]:
    """``data-dd-*`` attributes carrying one column's registry entry.

    This is the whole contract between the generator and the grid script: the
    script configures a column from these attributes, so a column it has never
    heard of still arrives with its filter, width, alignment and monospace
    intact.
    """
    spec = column_spec(header)
    attrs: List[Tuple[str, str]] = [("data-dd-key", field_key(header))]

    # The short spelling the grid puts in a narrow header. The `<th>` keeps the
    # full name — that is what print, site search and the column manager show,
    # and a printed table headed "Req" is worse than a narrow one.
    if "title" in spec:
        attrs.append(("data-dd-title", spec["title"]))
    for flag in ("mono", "identifier", "numeric", "clamp", "groupable", "compact"):
        if spec.get(flag):
            attrs.append((f"data-dd-{flag}", "true"))
    for name, attr in (
        ("filter", "data-dd-filter"),
        ("align", "data-dd-align"),
        ("links", "data-dd-links"),
    ):
        if spec.get(name):
            attrs.append((attr, str(spec[name])))
    for name, attr in (
        ("width", "data-dd-width"),
        ("min_width", "data-dd-min-width"),
        ("grow", "data-dd-grow"),
    ):
        if spec.get(name):
            attrs.append((attr, str(spec[name])))
    if spec.get("filter_values"):
        attrs.append(
            ("data-dd-filter-values", json.dumps(spec["filter_values"], indent=None))
        )
    if layout in spec.get("hidden_in", ()):
        attrs.append(("data-dd-hidden", "true"))
    return attrs


def glossary_for(columns: Iterable[str]) -> List[Tuple[str, str]]:
    """Definitions for the columns actually in use, in registry order."""
    wanted = set(columns)
    return [
        (name, spec["glossary"])
        for name, spec in COLUMN_REGISTRY.items()
        if name in wanted and spec.get("glossary")
    ]


# Distinguishes "not computed yet" from a computed None (no baseline on disk).
_UNSET = object()


class DataDictionaryExporter:
    """Builds the data dictionary page and workbook from dbt model metadata."""

    def __init__(
        self,
        project_root: Path,
        model_config: Dict[str, Any],
        export_config: Dict[str, Any],
    ):
        self.project_root = project_root
        self.model_config = model_config
        self.export_config = export_config
        self.generator = DocGenerator(project_root, model_config)
        self.rows: List[Dict[str, str]] = []
        self.source_commit: Optional[str] = None
        self.source_date: Optional[dt.datetime] = None
        self._changes: Any = _UNSET

    # ------------------------------------------------------------------
    # Source data
    # ------------------------------------------------------------------

    def collect(self):
        """Clone the dbt repo and flatten its models into one row per column."""
        repo_path = self.generator.clone_repository()
        self.source_commit = self._read_commit(repo_path)
        self.source_date = self._read_commit_date(repo_path)
        self.generator.parse_manifest(repo_path)
        self.generator.process_all_files(repo_path / self.model_config["yml_path"])
        self.rows = self.build_rows()
        print(f"Flattened {len(self.rows)} fields across {len(self.tables)} tables")

    @staticmethod
    def _read_commit(repo_path: Path) -> Optional[str]:
        """Short SHA of the dbt commit these docs were generated from."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    @staticmethod
    def _read_commit_date(repo_path: Path) -> Optional[dt.datetime]:
        """Commit date of the dbt source.

        Both artifacts are stamped with this rather than the wall clock, so
        re-running the generator against unchanged models reproduces the same
        bytes and leaves ``git status`` clean.
        """
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%cI"],
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            return dt.datetime.fromisoformat(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            return None

    @property
    def tables(self) -> List[Dict[str, Any]]:
        return self.generator.tables_data

    def build_rows(self) -> List[Dict[str, str]]:
        """One flat row per column, shared by both writers."""
        rows = []
        for table in self.tables:
            for column in table["columns"]:
                rows.append(
                    {
                        "Table": table["name"].upper(),
                        "Category": category_for(table["name"]),
                        "Field": column["name"],
                        "Required": is_required(column["constraints"]),
                        "Type": normalize_type(column["data_type"]),
                        "References": references_for(column["constraints"]),
                        "Description": flatten_description(column["description"]),
                    }
                )
        return rows

    def table_anchors(self) -> Dict[str, str]:
        """Upper-case table name -> the page anchor for its section.

        The only source of link targets on the page. A reference to a table
        that is not in this map — a dbt ``ref`` to something outside the
        baseline models — renders as plain text rather than as a dead link.

        Only identifier-shaped names get an entry. Every real dbt model name
        already is one, so the filter never fires in practice; it is here so
        that "the generator emits no href it did not construct from a known
        identifier" is a property of this code rather than of its input.
        """
        return {
            table["name"].upper(): table_anchor(table["name"].lower())
            for table in self.tables
            if ANCHOR_SAFE.fullmatch(table["name"])
        }

    def rows_for(self, table_name: str) -> List[Dict[str, str]]:
        upper = table_name.upper()
        return [row for row in self.rows if row["Table"] == upper]

    def index_rows(self) -> List[Dict[str, Any]]:
        return [
            {
                "Table": table["name"].upper(),
                "Category": category_for(table["name"]),
                "Fields": len(table["columns"]),
                "Description": flatten_description(table["description"]),
            }
            for table in self.tables
        ]

    # ------------------------------------------------------------------
    # Baseline comparison
    # ------------------------------------------------------------------

    def field_signatures(self) -> Dict[str, str]:
        """``TABLE.field`` -> the part of its definition worth watching.

        Type and requiredness are what break a query when they move.
        Descriptions are edited constantly and are deliberately not tracked:
        badging half the dictionary after a copy-editing pass would train
        everyone to ignore the badge.
        """
        return {
            f"{row['Table']}.{row['Field']}": f"{row['Type']}|{row['Required']}"
            for row in self.rows
        }

    def baseline_path(self) -> Path:
        return self.project_root / BASELINE_FILE

    def load_baseline(self) -> Optional[Dict[str, Any]]:
        path = self.baseline_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"Warning: {BASELINE_FILE} unreadable ({error}) — no change badges")
            return None
        if data.get("schema") != BASELINE_SCHEMA:
            print(f"Warning: {BASELINE_FILE} has an unknown schema — no change badges")
            return None
        return data

    def changes(self) -> Optional[Dict[str, Any]]:
        """New, retyped and dropped fields since the committed baseline.

        ``None`` when there is no baseline to compare against, which is also
        what the very first run sees.
        """
        if self._changes is not _UNSET:
            return self._changes
        baseline = self.load_baseline()
        if baseline is None:
            self._changes = None
            return None
        previous: Dict[str, str] = baseline.get("fields", {})
        current = self.field_signatures()
        self._changes = {
            "since": baseline.get("source_commit_date", "unknown"),
            "added": sorted(key for key in current if key not in previous),
            "changed": sorted(
                key
                for key, value in current.items()
                if key in previous and previous[key] != value
            ),
            "removed": sorted(key for key in previous if key not in current),
        }
        return self._changes

    def write_baseline(self):
        """Record the current schema as the baseline future runs compare to."""
        path = self.baseline_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": BASELINE_SCHEMA,
                    "model": self.export_config["cdm_version"],
                    "source_commit": self.source_commit or "unknown",
                    "source_commit_date": self.source_date_iso(),
                    "fields": self.field_signatures(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Baseline written to {path} ({len(self.rows)} fields)")

    def change_summary(self) -> Optional[str]:
        """One sentence describing the diff, or None when there is nothing."""
        changes = self.changes()
        if changes is None:
            return None
        counts = [
            (len(changes["added"]), "new"),
            (len(changes["changed"]), "changed"),
            (len(changes["removed"]), "removed"),
        ]
        if not any(count for count, _ in counts):
            return (
                f"No field has been added, retyped or dropped since {changes['since']}."
            )
        parts = ", ".join(f"{count} {label}" for count, label in counts if count)
        return f"Since the baseline of {changes['since']}: {parts}."

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    def source_date_iso(self) -> str:
        """Date of the dbt snapshot these definitions were taken from."""
        return self.source_date.date().isoformat() if self.source_date else "unknown"

    def provenance(self) -> List[tuple]:
        """Field/value pairs describing exactly what this snapshot documents.

        The full audit trail, for the web page. Readers there are one click from
        the repository, so naming the exact commit is useful; readers of the
        workbook are not, which is why ``workbook_provenance`` is shorter.
        """
        return [
            ("Data model", self.export_config["cdm_version"]),
            ("Source repository", REPO_URL),
            ("Source branch", "main"),
            ("Source commit", self.source_commit or "unknown"),
            ("Source commit date", self.source_date_iso()),
            ("Source path", self.model_config["yml_path"]),
            ("Generated by", "scripts/generate_exports.py"),
            ("Tables", str(len(self.tables))),
            ("Fields", str(len(self.rows))),
        ]

    def workbook_provenance(self) -> List[tuple]:
        """Field/value pairs for the workbook's About sheet.

        This file travels on its own — mailed around, parked in a shared drive —
        so it describes the tables it contains and how current they are, and
        leaves the repository, branch, commit and script name to the web page.

        ``Data as of`` is the date of the dbt snapshot, not the wall clock at
        build time. It answers the question a reader of a detached spreadsheet
        actually has (how stale is this?), and it keeps the workbook
        byte-reproducible so the pre-render hook does not dirty ``git status``
        on every preview.
        """
        return [
            ("Data model", self.export_config["cdm_version"]),
            ("Data as of", self.source_date_iso()),
            ("Tables", str(len(self.tables))),
            ("Fields", str(len(self.rows))),
        ]

    def workbook_created(self) -> dt.datetime:
        """The workbook's creation stamp, as a naive UTC datetime.

        ``git log --format=%cI`` carries the committer's UTC offset, and
        XlsxWriter writes this property with ``strftime("…%SZ")`` — the offset
        is dropped and the local time is labelled as UTC. Converting first
        keeps the stamp truthful, and it stays reproducible because the offset
        comes from the commit object rather than from this machine.
        """
        if not self.source_date:
            return dt.datetime(1980, 1, 1)
        if self.source_date.tzinfo is None:
            return self.source_date
        return self.source_date.astimezone(dt.timezone.utc).replace(tzinfo=None)

    def provenance_note(self) -> str:
        """Single sentence naming the variant these definitions describe."""
        return (
            f"These definitions describe the **{self.export_config['cdm_version']}** "
            "schema as defined in the dbt models, not the contents of any one "
            "released dataset. Individual released datasets (de-identified, "
            "limited, full) share this schema but differ in which rows and "
            "values they contain."
        )

    # ------------------------------------------------------------------
    # Excel workbook
    # ------------------------------------------------------------------

    def write_workbook(self):
        output_path = self.project_root / self.export_config["workbook_file"]
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with pd.ExcelWriter(
            output_path,
            engine="xlsxwriter",
            # XlsxWriter's write() converts strings that look like formulas or
            # URLs into live cells by default. Every string here is dbt prose,
            # so a description opening with "=" or "@" would land in Excel as a
            # formula rather than as the text it is, and one holding a bare URL
            # would become a clickable link nobody authored. Nothing in the
            # current source triggers either conversion; switching them off is
            # what keeps that true as the descriptions change. The About sheet's
            # deliberate links go through write_url(), which is unaffected.
            engine_kwargs={
                "options": {
                    "strings_to_formulas": False,
                    "strings_to_urls": False,
                }
            },
        ) as writer:
            book = writer.book
            # xlsxwriter otherwise stamps docProps/core.xml with the wall clock,
            # which would make every rebuild a new binary in git.
            book.set_properties(
                {
                    "title": self.export_config["workbook_title"],
                    "comments": self.export_config["page_description"],
                    "created": self.workbook_created(),
                }
            )
            fmt = self._formats(book)

            self._write_about_sheet(writer, fmt)
            self._write_index_sheet(writer, fmt)
            self._write_all_fields_sheet(writer, fmt)
            for table in self.tables:
                self._write_table_sheet(writer, fmt, table)

        print(f"Workbook written to {output_path}")

    @staticmethod
    def _formats(book) -> Dict[str, Any]:
        """The workbook's format table.

        Built once and passed down, because xlsxwriter deduplicates formats by
        identity rather than by definition: creating an equivalent format per
        sheet writes 46 near-identical entries into styles.xml.
        """
        return {
            "title": book.add_format(
                {
                    "bold": True,
                    "font_size": 14,
                    "font_color": BRAND_RED,
                }
            ),
            # Labels sit beside wrapped values that can run to several lines;
            # without valign the label falls to the bottom of the auto-fitted
            # row and reads as belonging to the row below.
            "label": book.add_format(
                {"bold": True, "font_color": BRAND_BLACK, "valign": "top"}
            ),
            "value": book.add_format({"text_wrap": True, "valign": "top"}),
            "header": book.add_format(
                {
                    "bold": True,
                    "font_color": "#FFFFFF",
                    "bg_color": BRAND_RED,
                    "border": 1,
                    "border_color": BRAND_RED,
                    "valign": "top",
                }
            ),
            "cell": book.add_format({"valign": "top"}),
            "wrap": book.add_format({"text_wrap": True, "valign": "top"}),
            # Excel's default hyperlink style is blue; this matches the site's
            # link colour instead, and keeps the underline so the cells still
            # read as clickable.
            "link": book.add_format(
                {
                    "font_color": BRAND_RED,
                    "underline": 1,
                    "valign": "top",
                }
            ),
        }

    def _write_about_sheet(self, writer, fmt):
        sheet = writer.book.add_worksheet("About")
        writer.sheets["About"] = sheet
        sheet.set_column(0, 0, 22)
        sheet.set_column(1, 1, 96)

        sheet.write(0, 0, self.export_config["workbook_title"], fmt["title"])

        row = 2
        for label, value in self.workbook_provenance():
            sheet.write(row, 0, label, fmt["label"])
            sheet.write(row, 1, value, fmt["value"])
            row += 1

        row += 1
        sheet.write(row, 0, "Scope", fmt["label"])
        sheet.write(
            row,
            1,
            "These definitions describe the schema as defined in the dbt models, "
            "not the contents of any one released dataset. Released datasets "
            "(de-identified, limited, full) share this schema but differ in which "
            "rows and values they contain.",
            fmt["value"],
        )
        row += 2

        sheet.write(row, 0, "Columns", fmt["label"])
        row += 1
        # Straight from the registry, so a column added there documents itself
        # here instead of quietly arriving in the sheets undefined.
        for label, value in glossary_for(
            set(INDEX_COLUMNS) | set(ALL_FIELDS_COLUMNS) | set(PAGE_COLUMNS)
        ):
            sheet.write(row, 0, label, fmt["label"])
            sheet.write(row, 1, value, fmt["value"])
            row += 1

        summary = self.change_summary()
        if summary:
            row += 1
            sheet.write(row, 0, "Changes", fmt["label"])
            sheet.write(row, 1, summary, fmt["value"])
            row += 1

        row += 1
        sheet.write(row, 0, "Sheets", fmt["label"])
        row += 1
        for label, value in (
            (
                "Index",
                "Every table, its category, field count, and description. "
                "Click a table name to jump to that table's sheet.",
            ),
            ("All Fields", "Every field in every table, in one flat list."),
            ("<TABLE>", "One sheet per table with its fields."),
        ):
            sheet.write(row, 0, label, fmt["label"])
            sheet.write(row, 1, value, fmt["value"])
            row += 1

    def _write_index_sheet(self, writer, fmt):
        frame = pd.DataFrame(self.index_rows(), columns=INDEX_COLUMNS)
        self._write_frame(writer, fmt, frame, "Index")

        # Overwrite the plain Table names with internal links to the per-table
        # sheets — 46 tabs do not fit the tab strip, so the Index is how anyone
        # actually navigates this workbook. Targets come from the same
        # `sheet_name_for` that named the sheets, so a link cannot drift from
        # the sheet it points at. Data starts at row 1; row 0 is the header.
        sheet = writer.sheets["Index"]
        for offset, name in enumerate(frame["Table"], start=1):
            sheet.write_url(
                offset,
                0,
                f"internal:'{sheet_name_for(name)}'!A1",
                fmt["link"],
                string=name,
            )

    def _write_all_fields_sheet(self, writer, fmt):
        frame = pd.DataFrame(self.rows, columns=ALL_FIELDS_COLUMNS)
        self._write_frame(writer, fmt, frame, "All Fields")

    def _write_table_sheet(self, writer, fmt, table):
        frame = pd.DataFrame(self.rows_for(table["name"]), columns=PAGE_COLUMNS)
        self._write_frame(writer, fmt, frame, sheet_name_for(table["name"]))

    @staticmethod
    def _write_frame(writer, fmt, frame, sheet_name):
        """Write one DataFrame as a frozen, filterable, wrapped sheet.

        Widths and the wrapping format are looked up by column *name*. They
        used to be a positional list per sheet with "the last column is always
        Description" baked into the wrap: inserting a column shifted every
        width onto its neighbour, and adding one after Description moved the
        wrap onto it and turned every description into a single unreadable
        line. Neither raised an error.
        """
        frame.to_excel(
            writer, sheet_name=sheet_name, index=False, startrow=1, header=False
        )
        sheet = writer.sheets[sheet_name]

        for col, name in enumerate(frame.columns):
            sheet.write(0, col, name, fmt["header"])

        for col, name in enumerate(frame.columns):
            spec = column_spec(name)
            style = fmt["wrap"] if spec.get("wrap") else fmt["cell"]
            sheet.set_column(col, col, spec.get("xlsx", 20), style)

        last_col = len(frame.columns) - 1
        sheet.freeze_panes(1, 0)
        if len(frame):
            sheet.autofilter(0, 0, len(frame), last_col)

    # ------------------------------------------------------------------
    # Quarto page
    # ------------------------------------------------------------------

    def write_page(self):
        output_path = self.project_root / self.export_config["page_file"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self.generate_page(), encoding="utf-8")
        print(f"Page written to {output_path}")

    def generate_page(self) -> str:
        cfg = self.export_config
        workbook_href = str(
            Path(cfg["workbook_file"]).relative_to(Path(cfg["page_file"]).parent)
        )

        lines = [
            "---",
            f'title: "{cfg["page_title"]}"',
            f'description: "{cfg["page_description"]}"',
            *GRID_FRONT_MATTER,
            "---",
            "",
            "<!-- GENERATED FILE — do not edit by hand.",
            "     Produced by scripts/generate_exports.py; see README.md. -->",
            "",
        ]
        lines.extend(self._page_intro(workbook_href))
        lines.extend(self._page_changes())
        lines.extend(self._page_all_fields())
        lines.extend(self._page_index())
        for table in self.tables:
            lines.extend(self._page_table_section(table))
        return "\n".join(lines)

    def _page_intro(self, workbook_href: str) -> List[str]:
        provenance = "\n".join(
            f"| {label} | {value} |" for label, value in self.provenance()
        )
        # The column definitions come from the registry rather than from a
        # hand-written paragraph, which is what used to go stale whenever a
        # column was added.
        glossary = "\n".join(
            f"| **{label}** | {value} |"
            for label, value in glossary_for(
                set(ALL_FIELDS_COLUMNS) | set(INDEX_COLUMNS) | set(PAGE_COLUMNS)
            )
        )
        summary = self.change_summary()
        return [
            f"Every table and field in the {self.export_config['cdm_version']} "
            "implementation, in one searchable page. Start in **All Fields** "
            "to search every table at once, or pick a single table from the "
            "list. Each one is a grid: search it, filter any "
            "column from the box under its header, click a header to sort, "
            "choose which columns to show, group the rows, click a long "
            "description to expand it, click a row for its full detail, and "
            "download whatever you have filtered down to as CSV. Take "
            "the whole thing away as an Excel workbook with the button below.",
            "",
            f'<a class="btn btn-primary data-dictionary-download" '
            f'href="{workbook_href}" download>Download as Excel (.xlsx)</a>',
            "",
            '::: {.callout-note collapse="true"}',
            "## What this describes",
            "",
            self.provenance_note(),
            "",
            "| | |",
            "|---|---|",
            provenance,
            "",
            *([summary, ""] if summary else []),
            "### Columns",
            "",
            "| | |",
            "|---|---|",
            glossary,
            "",
            ":::",
            "",
            "For what each table is for and how Stanford populates it, see "
            f"[{self.export_config['cdm_version']} Data Model]"
            f"({self.export_config['model_page']}). The fields are here; the "
            "narrative is there.",
            "",
            "---",
            "",
        ]

    def _page_changes(self) -> List[str]:
        """The new/changed field list, as JSON the grid script can read.

        A data island rather than extra columns: the badge belongs to a field's
        identity, not to its definition, and keeping it out of the table leaves
        the static markup, the workbook and llms-full.txt unchanged.
        """
        changes = self.changes()
        if changes is None:
            return []
        payload = json.dumps(
            {
                "since": changes["since"],
                "added": changes["added"],
                "changed": changes["changed"],
                "removed": changes["removed"],
            },
            sort_keys=True,
        )
        # A dbt field name cannot contain "<", but escaping it is what keeps
        # that a property of the output rather than of the input: no value can
        # close this script element early, whatever it holds.
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
        payload = payload.replace("&", "\\u0026")
        return [
            f'<script type="application/json" id="dd-changes">{payload}</script>',
            "",
        ]

    def _page_all_fields(self) -> List[str]:
        return [
            "## All Fields {#all-fields}",
            "",
            f"All {len(self.rows)} fields across {len(self.tables)} tables. "
            "Search here to find which table contains a given field.",
            "",
            self._to_html(self.rows, ALL_FIELDS_COLUMNS, "dt-all-fields", LAYOUT_ALL),
            "",
            "---",
            "",
        ]

    def _page_index(self) -> List[str]:
        rows = self.index_rows()
        table = self._to_html(rows, INDEX_COLUMNS, "dt-index", LAYOUT_INDEX)

        return [
            "## Tables {#tables}",
            "",
            f"{len(rows)} tables. Click a table name to open its fields.",
            "",
            table,
            "",
            "---",
            "",
        ]

    def _page_table_section(self, table: Dict[str, Any]) -> List[str]:
        name = table["name"]
        rows = self.rows_for(name)
        lines = [
            f"## {table_heading(name)} {{#{table_anchor(name)}}}",
            "",
        ]
        if category_for(name) == EXTENSION_CATEGORY:
            lines.extend(
                [
                    f"*Stanford extension — not part of the standard "
                    f"{self.export_config['cdm_version']}.*",
                    "",
                ]
            )
        # This paragraph is the one place on the page where a dbt value is
        # emitted as Markdown rather than through the HTML escaping below, so
        # it is escaped here instead.
        description = flatten_description(table["description"])
        if description:
            lines.extend([markdown_prose(description), ""])
        lines.extend(
            [
                f"{len(rows)} fields.",
                "",
                self._to_html(
                    rows,
                    PAGE_COLUMNS,
                    f"dt-{table_anchor(name)}",
                    LAYOUT_FIELDS,
                    # The rows here carry no Table column, so the grid is told
                    # which table it is showing. Change badges are keyed
                    # `TABLE.field` and would otherwise have to guess it back
                    # out of the section anchor, which strips the leading
                    # underscore from Stanford extensions.
                    table_name=name.upper(),
                ),
                "",
                "---",
                "",
            ]
        )
        return lines

    # ------------------------------------------------------------------
    # Static HTML
    # ------------------------------------------------------------------

    @staticmethod
    def _attrs(pairs: Iterable[Tuple[str, Any]]) -> str:
        """Attribute string with every value escaped, quotes included."""
        return "".join(
            f' {name}="{html.escape(str(value), quote=True)}"'
            for name, value in pairs
            if value is not None
        )

    def _cell_html(self, header: str, value: Any, anchors: Dict[str, str]) -> str:
        """One ``<td>``'s inner HTML.

        Everything is escaped. The single exception is a column the registry
        marks ``links: "table"``, whose values are matched against the table
        names this run produced — so the only markup that can appear is an
        anchor this method built, pointing at a section that exists.
        """
        text = "" if value is None else str(value)
        if column_spec(header).get("links") != "table":
            return html.escape(text)

        parts = []
        for token in (piece.strip() for piece in text.split(",")):
            if not token:
                continue
            anchor = anchors.get(token.upper())
            if anchor:
                parts.append(
                    f'<a href="#{html.escape(anchor, quote=True)}">'
                    f"{html.escape(token)}</a>"
                )
            else:
                parts.append(html.escape(token))
        return ", ".join(parts)

    def _to_html(
        self,
        rows: List[Dict[str, Any]],
        columns: List[str],
        table_id: str,
        layout: str,
        table_name: Optional[str] = None,
    ) -> str:
        """The static table: what search, print and no-JS visitors get.

        Written out here rather than through ``DataFrame.to_html`` so each
        ``<th>`` can carry its registry entry as ``data-dd-*`` attributes.
        That is what lets the grid script configure a column it has never heard
        of, and what lets the stylesheet put the monospace face on cells by
        class instead of by ``nth-child`` position.

        Escaping is not optional: every cell originates in dbt YAML, so a
        description containing markup must render as text. ``_cell_html`` is
        the only thing that emits markup, and only for a table name it
        recognises.

        The ``.dd-static`` wrapper is the scroll container. The table stays in
        the document — the grid script hides it only once its replacement has
        rendered — so site search, llms-full.txt, printing and visitors without
        JavaScript all still see the data.
        """
        anchors = self.table_anchors()
        out = [
            '<div class="dd-static">',
            "<table"
            + self._attrs(
                [
                    ("id", table_id),
                    (
                        "class",
                        "table table-sm table-striped table-hover "
                        f"data-dictionary-table {layout}",
                    ),
                    ("data-dd-table", table_name),
                    # Stem for this grid's CSV download. Lives here rather than
                    # in data-dictionary.js so that script carries no per-site
                    # constant and both documentation sites can run the same one.
                    ("data-dd-csv", self.export_config["csv_prefix"]),
                ]
            )
            + ">",
            "<thead>",
            "<tr>",
        ]
        for header in columns:
            out.append(
                "<th"
                + self._attrs([("scope", "col")] + column_attributes(header, layout))
                + f">{html.escape(header)}</th>"
            )
        out.extend(["</tr>", "</thead>", "<tbody>"])

        mono = {header: column_spec(header).get("mono") for header in columns}
        align = {header: column_spec(header).get("align") for header in columns}
        for row in rows:
            out.append("<tr>")
            for header in columns:
                classes = " ".join(
                    part
                    for part in (
                        "dd-mono" if mono[header] else "",
                        f"dd-align-{align[header]}" if align[header] else "",
                    )
                    if part
                )
                out.append(
                    "<td"
                    + self._attrs([("class", classes or None)])
                    + f">{self._cell_html(header, row.get(header, ''), anchors)}</td>"
                )
            out.append("</tr>")

        out.extend(["</tbody>", "</table>", "</div>"])
        return "\n".join(out)

    # ------------------------------------------------------------------

    def run(self, update_baseline: bool = False):
        try:
            print("=" * 60)
            print(f"Generating: {self.export_config['page_title']}")
            print("=" * 60)

            self.collect()
            if update_baseline:
                # Before the page, so the page it writes reports an empty diff
                # against the baseline it just recorded.
                self.write_baseline()
                self._changes = _UNSET
            summary = self.change_summary()
            if summary:
                print(summary)
            self.write_page()
            self.write_workbook()

            print("=" * 60)
            print("Data dictionary generation complete!")
            print("=" * 60)
        except Exception as e:
            print(f"Error during data dictionary generation: {e}")
            import traceback

            traceback.print_exc()
            sys.exit(1)
        finally:
            self.generator.cleanup()


def main():
    parser = argparse.ArgumentParser(
        description="Generate the STARR-OMOP data dictionary page and workbook"
    )
    parser.add_argument(
        "model",
        choices=sorted(EXPORT_CONFIGS),
        help="Which data model to export",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            f"Record the current schema in {BASELINE_FILE} as the point future "
            "runs compare against. Run this at release time and commit the "
            "result; the pre-render hook never touches it."
        ),
    )
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    exporter = DataDictionaryExporter(
        project_root, MODEL_CONFIGS[args.model], EXPORT_CONFIGS[args.model]
    )
    exporter.run(update_baseline=args.update_baseline)


if __name__ == "__main__":
    main()
