#!/usr/bin/env python3
"""
Data dictionary exporter for STARR-OMOP dbt models.

Produces two artifacts from the same dbt source the rest of the site uses:

    docs/omop_data_dictionary.qmd                   searchable page
    docs/downloads/starr_omop_data_dictionary.xlsx  downloadable workbook

Both are generated and committed. This generator is deliberately *not* wired
into the Quarto ``pre-render`` hooks in ``docs/_quarto.yml``: the workbook is a
binary file, and rewriting it on every local preview would leave noise in every
``git status``. Run it by hand when the dbt models change, then commit both
artifacts.

Usage:
    python generate_exports.py omop

Note: Activate the virtual environment before running:
    source .venv/bin/activate
"""

import argparse
import datetime as dt
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

DATATABLES_VERSION = "9.0.3"
DATATABLES_CDN = f"https://cdn.jsdelivr.net/npm/simple-datatables@{DATATABLES_VERSION}"

# Emitted into the generated page's own front matter rather than into
# `format.html` in _quarto.yml, so the only page that needs simple-datatables
# is the only page that pays to download it.
#
# Inline `text:` rather than `file:` partials: the root .gitignore drops *.js
# and *.css, so partial files would not survive a fresh clone.
DATATABLES_FRONT_MATTER = [
    "include-in-header:",
    "  text: |",
    f'    <link rel="stylesheet" href="{DATATABLES_CDN}/dist/style.css">',
    "include-after-body:",
    "  text: |",
    '    <script type="module">',
    f'      import {{DataTable}} from "{DATATABLES_CDN}/dist/module.js";',
    "      for (const table of document.querySelectorAll(\"table[id^='dt-']\")) {",
    "        new DataTable(table, {",
    "          searchable: true,",
    "          sortable: true,",
    "          perPage: 25,",
    "          perPageSelect: [10, 25, 50, 100],",
    '          labels: {placeholder: "Search…", noRows: "No matching fields"}',
    "        });",
    "      }",
    "    </script>",
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

    def provenance(self) -> List[tuple]:
        """Field/value pairs describing exactly what this snapshot documents."""
        commit = self.source_commit or "unknown"
        commit_date = (
            self.source_date.date().isoformat() if self.source_date else "unknown"
        )
        return [
            ("Data model", self.export_config["cdm_version"]),
            ("Source repository", REPO_URL),
            ("Source branch", "main"),
            ("Source commit", commit),
            ("Source commit date", commit_date),
            ("Source path", self.model_config["yml_path"]),
            ("Generated by", "scripts/generate_exports.py"),
            ("Tables", str(len(self.tables))),
            ("Fields", str(len(self.rows))),
        ]

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

        with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
            book = writer.book
            # xlsxwriter otherwise stamps docProps/core.xml with the wall clock,
            # which would make every rebuild a new binary in git.
            book.set_properties(
                {
                    "title": self.export_config["workbook_title"],
                    "comments": self.export_config["page_description"],
                    "created": self.source_date or dt.datetime(1980, 1, 1),
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
                "label": book.add_format({"bold": True, "font_color": BRAND_BLACK}),
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
        for label, value in self.provenance():
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
            ("Index", "Every table, its category, field count, and description."),
            ("All Fields", "Every field in every table, in one flat list."),
            ("<TABLE>", "One sheet per table with its fields."),
        ):
            sheet.write(row, 0, label, fmt["label"])
            sheet.write(row, 1, value, fmt["value"])
            row += 1

        row += 1
        sheet.write(row, 0, "Regenerate", fmt["label"])
        sheet.write(row, 1, "python scripts/generate_exports.py omop", fmt["value"])

    def _write_index_sheet(self, writer, fmt):
        frame = pd.DataFrame(self.index_rows(), columns=INDEX_COLUMNS)
        self._write_frame(writer, fmt, frame, "Index", [22, 18, 8, 90])

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
            *DATATABLES_FRONT_MATTER,
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
            "implementation, in one searchable page. Use the search box on any "
            "table to filter it, or click a column header to sort.",
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
        for row in rows:
            anchor = table_anchor(row["Table"].lower())
            row["Table"] = f'<a href="#{anchor}">{row["Table"]}</a>'
        frame = pd.DataFrame(rows, columns=INDEX_COLUMNS)
        return [
            "## Tables {#tables}",
            "",
            f"{len(rows)} tables. Click a table name to jump to its fields.",
            "",
            # Table names are anchor markup here, so they must not be escaped.
            self._to_html(frame, "dt-index", "dd-layout-index", escape=False),
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
        description = flatten_description(table["description"])
        if description:
            lines.extend([description, ""])
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
    def _to_html(
        frame: pd.DataFrame, table_id: str, layout: str, escape: bool = True
    ) -> str:
        # The layout class tells the stylesheet which columns hold identifiers.
        # The three layouts have different column orders, so CSS cannot key off
        # position alone (see docs/styles.css).
        return frame.to_html(
            index=False,
            escape=escape,
            border=0,
            classes=(
                "table table-sm table-striped table-hover "
                f"data-dictionary-table {layout}"
            ),
            justify="left",
            table_id=table_id,
        )

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
