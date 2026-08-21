#!/usr/bin/env python3
"""
Data dictionary exporter for STARR-OMOP dbt models.

Produces two artifacts from the same dbt source the rest of the site uses:

    docs/omop_data_dictionary.qmd                   searchable page
    docs/downloads/starr_omop_data_dictionary.xlsx  downloadable workbook

Both are produced by a ``pre-render`` hook in ``docs/_quarto.yml``, so they
refresh on every ``quarto render``/``preview``/``publish`` like the rest of the
generated site. Both are also committed, so a fresh clone serves the page and
its download without a build.

Rewriting a binary on every preview would normally leave noise in ``git
status``; it does not here because both artifacts are byte-reproducible. Every
timestamp written into them derives from the dbt source commit rather than the
wall clock (see ``_read_commit_date`` and ``workbook_created``), so re-running
against unchanged models reproduces the same bytes and only a real dbt change
shows up as a diff.

Usage:
    python scripts/generate_exports.py omop

Note: Activate the virtual environment before running:
    source .venv/bin/activate
"""

import argparse
import datetime as dt
import html
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

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

PAGE_COLUMNS = ["Field", "Required", "Type", "Description"]
ALL_FIELDS_COLUMNS = ["Table", "Category", "Field", "Required", "Type", "Description"]
INDEX_COLUMNS = ["Table", "Category", "Fields", "Description"]

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

# Six columns of identifiers and free text do not fit the ~800px article
# column Quarto's grid reserves for body content, and `page-layout: full`
# alone does not change that: the theme bakes `minmax(500px, calc(800px - 3em))`
# into the body-content track, and the page-layout class has no CSS of its own.
# Overriding `grid.body-width` for this page is the supported way to change it
# (Quarto compiles a second theme bundle for the page, which is why this is set
# here and not site-wide). `margin-width: 0` reclaims the right-hand margin,
# and the table of contents goes with it — the Tables grid below is a better
# index of a 43-section page than a 45-entry sidebar list.
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
                        "Description": flatten_description(column["description"]),
                    }
                )
        return rows

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
            fmt = {
                "title": book.add_format(
                    {
                        "bold": True,
                        "font_size": 14,
                        "font_color": BRAND_RED,
                    }
                ),
                # Labels sit beside wrapped values that can run to several
                # lines; without valign the label falls to the bottom of the
                # auto-fitted row and reads as belonging to the row below.
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
                # Excel's default hyperlink style is blue; this matches the
                # site's link colour instead, and keeps the underline so the
                # cells still read as clickable.
                "link": book.add_format(
                    {
                        "font_color": BRAND_RED,
                        "underline": 1,
                        "valign": "top",
                    }
                ),
            }

            self._write_about_sheet(writer, fmt)
            self._write_index_sheet(writer, fmt)
            self._write_all_fields_sheet(writer, fmt)
            for table in self.tables:
                self._write_table_sheet(writer, fmt, table)

        print(f"Workbook written to {output_path}")

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
        for label, value in (
            ("Field", "Column name as defined in the dbt model."),
            ("Required", "Yes when dbt declares the column a primary key or not null."),
            ("Type", "BigQuery data type, canonicalised to a single spelling."),
            ("Description", "Column description from the dbt model."),
            (
                "Category",
                f"{CORE_CATEGORY} or {EXTENSION_CATEGORY} "
                "(underscore-prefixed tables are Stanford additions).",
            ),
        ):
            sheet.write(row, 0, label, fmt["label"])
            sheet.write(row, 1, value, fmt["value"])
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
        self._write_frame(writer, fmt, frame, "Index", [22, 18, 8, 90])

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
        self._write_frame(writer, fmt, frame, "All Fields", [22, 18, 32, 10, 14, 90])

    def _write_table_sheet(self, writer, fmt, table):
        frame = pd.DataFrame(self.rows_for(table["name"]), columns=PAGE_COLUMNS)
        self._write_frame(
            writer, fmt, frame, sheet_name_for(table["name"]), [32, 10, 14, 96]
        )

    @staticmethod
    def _write_frame(writer, fmt, frame, sheet_name, widths):
        """Write one DataFrame as a frozen, filterable, wrapped sheet."""
        frame.to_excel(
            writer, sheet_name=sheet_name, index=False, startrow=1, header=False
        )
        sheet = writer.sheets[sheet_name]

        for col, name in enumerate(frame.columns):
            sheet.write(0, col, name, fmt["header"])

        last_col = len(frame.columns) - 1
        for col, width in enumerate(widths[: last_col + 1]):
            # The final column is always Description; wrap it.
            style = fmt["wrap"] if col == last_col else fmt["cell"]
            sheet.set_column(col, col, width, style)

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
        lines.extend(self._page_all_fields())
        lines.extend(self._page_index())
        for table in self.tables:
            lines.extend(self._page_table_section(table))
        return "\n".join(lines)

    def _page_intro(self, workbook_href: str) -> List[str]:
        provenance = "\n".join(
            f"| {label} | {value} |" for label, value in self.provenance()
        )
        return [
            f"Every table and field in the {self.export_config['cdm_version']} "
            "implementation, in one searchable page. Start in **All Fields** "
            "to search every table at once, or pick a single table from the "
            "list. Each one is a grid: search it, filter any "
            "column from the box under its header, click a header to sort, "
            "drag a column edge to resize, click a long description to expand "
            "it, and download whatever you have filtered down to as CSV. Take "
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
            ":::",
            "",
            "**Required** is `Yes` when the dbt model declares the field a primary "
            "key or not null, and `No` otherwise. **Category** distinguishes core "
            f"OMOP tables from Stanford additions, which are underscore-prefixed. "
            "For the narrative table notes and per-column constraint detail, see "
            f"[{self.export_config['cdm_version']} Data Model]"
            f"({self.export_config['model_page']}).",
            "",
            "---",
            "",
        ]

    def _page_all_fields(self) -> List[str]:
        frame = pd.DataFrame(self.rows, columns=ALL_FIELDS_COLUMNS)
        return [
            "## All Fields {#all-fields}",
            "",
            f"All {len(self.rows)} fields across {len(self.tables)} tables. "
            "Search here to find which table contains a given field.",
            "",
            self._to_html(frame, "dt-all-fields", "dd-layout-all"),
            "",
            "---",
            "",
        ]

    def _page_index(self) -> List[str]:
        rows = self.index_rows()
        # Every column is rendered with escaping on, Category and Description
        # included: both carry dbt YAML prose, and markup in a description must
        # reach the page as text rather than as elements. The Table cell needs
        # anchor markup that escaping would destroy, so it travels through the
        # frame as a sentinel and is substituted afterwards. NUL cannot appear
        # in a YAML scalar, so no dbt value can forge one, and the anchor built
        # here is the only markup that gets in.
        links: Dict[str, str] = {}
        for position, row in enumerate(rows):
            # Dropping NUL from the data makes the sentinel unforgeable by
            # construction. A YAML scalar cannot hold one, so this changes no
            # real output; it turns "the source cannot do that" from an
            # assumption into something the code enforces.
            for column in ("Category", "Description"):
                row[column] = str(row[column]).replace("\x00", "")
            token = f"\x00dd-link-{position}\x00"
            name = row["Table"].replace("\x00", "")
            anchor = html.escape(table_anchor(name.lower()))
            links[token] = f'<a href="#{anchor}">{html.escape(name)}</a>'
            row["Table"] = token

        frame = pd.DataFrame(rows, columns=INDEX_COLUMNS)
        table = self._to_html(frame, "dt-index", "dd-layout-index")
        for token, link in links.items():
            if table.count(token) != 1:
                raise RuntimeError(f"index link sentinel {token!r} did not survive")
            table = table.replace(token, link)

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
        frame = pd.DataFrame(self.rows_for(name), columns=PAGE_COLUMNS)
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
        # emitted as Markdown rather than through pandas' HTML escaping, so it
        # is escaped here instead.
        description = flatten_description(table["description"])
        if description:
            lines.extend([markdown_prose(description), ""])
        lines.extend(
            [
                f"{len(frame)} fields.",
                "",
                self._to_html(frame, f"dt-{table_anchor(name)}", "dd-layout-fields"),
                "",
                "---",
                "",
            ]
        )
        return lines

    @staticmethod
    def _to_html(frame: pd.DataFrame, table_id: str, layout: str) -> str:
        # The layout class does double duty: it tells the stylesheet which
        # columns hold identifiers (the three layouts have different column
        # orders, so CSS cannot key off position alone) and it tells
        # docs/assets/data-dictionary.js how to configure the grid it builds
        # from this table.
        #
        # Escaping is not optional: every cell here originates in dbt YAML, so
        # a description containing markup must render as text. _page_index gets
        # its links past this with a sentinel rather than by turning it off.
        #
        # The `.dd-static` wrapper is the scroll container for the table as
        # rendered here. That table stays in the document: the grid script
        # hides it only after its replacement has rendered, so site search,
        # llms-full.txt, printing and no-JS visitors all still see the data.
        table = frame.to_html(
            index=False,
            escape=True,
            border=0,
            classes=(
                "table table-sm table-striped table-hover "
                f"data-dictionary-table {layout}"
            ),
            justify="left",
            table_id=table_id,
        )
        return f'<div class="dd-static">\n{table}</div>'

    # ------------------------------------------------------------------

    def run(self):
        try:
            print("=" * 60)
            print(f"Generating: {self.export_config['page_title']}")
            print("=" * 60)

            self.collect()
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
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    exporter = DataDictionaryExporter(
        project_root, MODEL_CONFIGS[args.model], EXPORT_CONFIGS[args.model]
    )
    exporter.run()


if __name__ == "__main__":
    main()
