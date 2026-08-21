#!/usr/bin/env python3
"""
Documentation generator for STARR-OMOP dbt models.

Generates Quarto markdown documentation from dbt YML files for the
STARR-OMOP data model.

Usage:
    python scripts/generate_docs.py omop

Note: Activate the virtual environment before running:
    source .venv/bin/activate
"""

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    print("Error: PyYAML not found.")
    print("Please activate the virtual environment: source .venv/bin/activate")
    print("Or install dependencies: uv sync")
    sys.exit(1)


REPO_URL = "https://github.com/susom/starr-data-lake.git"

MODEL_CONFIGS = {
    "omop": {
        "yml_path": "dbt/omop_cdm54/models/baseline",
        "manifest_path": None,
        "output_file": "docs/omop_data_model.qmd",
        "title": "OMOP CDM v5.4 Data Model",
        "description": "Detailed table and column definitions",
        "overview": (
            "This page documents all tables in the STARR-OMOP CDM v5.4 implementation. "
            "Each table includes Stanford-specific "
            "implementation notes and detailed column descriptions."
        ),
        "bq_project": None,
        "bq_schema": None,
    },
}

EXCLUDE_FOLDERS = ["temp"]


def table_anchor(name: str) -> str:
    """Quarto anchor id for a table.

    Leading underscores are stripped so section links resolve
    (``_variant_occurrence`` -> ``#variant_occurrence``).
    """
    return name.lstrip("_")


def table_heading(name: str) -> str:
    """Display heading for a table.

    Leading underscores are escaped so they render literally instead of
    being read as emphasis (``_variant_occurrence`` -> ``\\_VARIANT_OCCURRENCE``).
    """
    stripped = name.lstrip("_")
    return "\\_" * (len(name) - len(stripped)) + stripped.upper()


# A URL scheme Pandoc may keep in an href. Anything else -- javascript:,
# data:, vbscript: -- is a way to run code from a link.
SAFE_URL_SCHEME = re.compile(r"\A(?:https?|mailto|ftp):", re.IGNORECASE)
ANY_URL_SCHEME = re.compile(r"\A[A-Za-z][A-Za-z0-9+.\-]*:")

# One link, spelled the simple way, in text whose brackets are already escaped.
#
# The label deliberately admits no bracket and no backslash. CommonMark allows
# both -- balanced nested brackets, and `\]` standing for a literal one -- and a
# label using either stays escaped rather than being matched loosely. That is
# the whole point of the rewrite below: the shapes this expression cannot read
# are the shapes it must not approve.
ESCAPED_LINK = re.compile(r"\\\[([^\[\]\\\n]{0,200})\\\]\(([^\s()<>\\]{1,300})\)")


def _restore_if_safe(match: "re.Match[str]") -> str:
    """Turn an escaped ``\\[label\\](dest)`` back into a live link, or leave it.

    Called only on the strict shape above, so the one remaining question is the
    destination. A relative target has no scheme and is fine; an absolute one
    has to name a scheme that cannot execute.
    """
    label, destination = match.group(1), match.group(2)
    if ANY_URL_SCHEME.match(destination) and not SAFE_URL_SCHEME.match(destination):
        return match.group(0)
    return f"[{label}]({destination})"


def markdown_prose(text: str) -> str:
    """dbt prose, made safe to drop into a generated Markdown document.

    Descriptions are authored in ``starr-data-lake`` and land in these pages as
    Markdown, which Pandoc reads with more power than prose needs. This takes
    that power away and hands back only what the descriptions actually use.

    The ordering is the design. Everything that can start a construct is
    escaped first -- HTML metacharacters, backslashes, braces, and *every*
    bracket -- so by this point no link, image, reference definition, attribute
    span or raw-HTML block can form at all. Only then is a single narrow shape,
    ``[label](destination)`` with a scheme that cannot execute, allowed back.

    That direction matters more than the patterns do. Matching link syntax and
    escaping the ones that look dangerous fails open: every label spelling the
    expression cannot parse -- ``[a [b]](...)``, ``[x \\]](...)`` -- is a way
    past it, and CommonMark has more of those than a regular expression can
    hold. Escaping first and restoring second fails closed, so an unparseable
    label is rendered as the text somebody wrote instead of becoming a link.

    What survives is what these descriptions contain: code spans around table
    names, emphasis, and ordinary http(s) links to the OHDSI vocabulary
    browser. A bracketed aside that was never a link, like
    ``[anything other than the patient]``, still reads exactly as written.

    This narrows an already-trusted source rather than standing between the
    site and an untrusted one -- anyone who can edit a dbt description can
    already change what the data dictionary claims. Constraining the Pandoc
    reader for these pages would be the airtight version.
    """
    safe = html.escape(text, quote=False)
    # Before adding escapes of our own, so a backslash in the source stays a
    # backslash instead of escaping the character behind it.
    safe = safe.replace("\\", "\\\\")
    safe = safe.replace("{", "\\{").replace("}", "\\}")
    safe = safe.replace("[", "\\[").replace("]", "\\]")
    return ESCAPED_LINK.sub(_restore_if_safe, safe)


class DocGenerator:
    """Generates STARR documentation from dbt YML files."""

    def __init__(self, project_root: Path, config: Dict[str, Any]):
        self.project_root = project_root
        self.config = config
        self.temp_dir: Optional[str] = None
        self.tables_data: List[Dict[str, Any]] = []
        self.bq_locations: Dict[str, str] = {}

    def clone_repository(self) -> Path:
        """Clone repository with sparse checkout."""
        print("Cloning starr-data-lake repository...")

        self.temp_dir = tempfile.mkdtemp(prefix="starr_docs_")
        repo_path = Path(self.temp_dir) / "starr-data-lake"

        sparse_paths = [self.config["yml_path"]]
        if self.config["manifest_path"]:
            sparse_paths.append(self.config["manifest_path"])

        try:
            subprocess.run(
                [
                    "git", "clone", "--depth", "1",
                    "--filter=blob:none", "--sparse",
                    "--branch", "main",
                    REPO_URL, str(repo_path),
                ],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "sparse-checkout", "set"] + sparse_paths,
                cwd=repo_path, check=True, capture_output=True, text=True,
            )
            print(f"Repository cloned to {repo_path}")
            return repo_path
        except subprocess.CalledProcessError as e:
            print(f"Error cloning repository: {e}")
            print(f"STDOUT: {e.stdout}")
            print(f"STDERR: {e.stderr}")
            sys.exit(1)

    def parse_manifest(self, repo_path: Path):
        """Parse manifest.json to extract BigQuery locations."""
        if not self.config["manifest_path"]:
            return

        manifest_file = repo_path / self.config["manifest_path"]
        if not manifest_file.exists():
            print("manifest.json not found — skipping BigQuery locations")
            return

        bq_project = self.config["bq_project"]
        bq_schema = self.config["bq_schema"]
        yml_path = self.config["yml_path"]

        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        for key, node in manifest.get("nodes", {}).items():
            if not key.startswith("model.") or node.get("resource_type") != "model":
                continue
            original_path = node.get("original_file_path", "")
            if yml_path not in original_path:
                continue
            schema = node.get("schema", "")
            if "staging" in schema or "temp" in schema:
                continue
            name = node.get("name", "")
            self.bq_locations[name] = f"{bq_project}.{bq_schema}.{name}"

        print(f"Loaded BigQuery locations for {len(self.bq_locations)} tables")

    def find_yml_files(self, yml_path: Path) -> List[Path]:
        """Find all YML files, excluding specified folders."""
        yml_files = []
        for yml_file in yml_path.rglob("*.yml"):
            relative_path = yml_file.relative_to(yml_path)
            if any(excluded in relative_path.parts for excluded in EXCLUDE_FOLDERS):
                continue
            yml_files.append(yml_file)
        print(f"Found {len(yml_files)} YML files to process")
        return sorted(yml_files)

    def parse_yml_file(self, yml_file: Path) -> List[Dict[str, Any]]:
        """Parse a single YML file and extract model information."""
        try:
            with open(yml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data or "models" not in data:
                return []

            tables = []
            for model in data["models"]:
                table_info = {
                    "name": model.get("name", ""),
                    "description": model.get("description", "").strip(),
                    "config": model.get("config", {}),
                    "columns": [],
                }
                for column in model.get("columns", []):
                    table_info["columns"].append({
                        "name": column.get("name", ""),
                        "description": column.get("description", "").strip(),
                        "data_type": column.get("data_type", ""),
                        "constraints": self._parse_constraints(
                            column.get("constraints", [])
                        ),
                    })
                tables.append(table_info)
            return tables
        except Exception as e:
            print(f"Warning: Failed to parse {yml_file.name}: {e}")
            return []

    def _parse_constraints(self, constraints: List[Dict]) -> List[str]:
        """Parse constraints into human-readable format."""
        result = []
        for c in constraints:
            ctype = c.get("type", "")
            if ctype == "primary_key":
                result.append("**Primary Key**")
            elif ctype == "not_null":
                result.append("**Not Null**")
            elif ctype == "unique":
                result.append("**Unique**")
            elif ctype == "foreign_key":
                to_table = c.get("to", "")
                to_columns = c.get("to_columns", [])
                if to_columns:
                    result.append(f"**Foreign Key** → `{to_table}({', '.join(to_columns)})`")
                else:
                    result.append(f"**Foreign Key** → `{to_table}`")
        return result

    def process_all_files(self, yml_path: Path):
        """Process all YML files and collect table data."""
        for yml_file in self.find_yml_files(yml_path):
            self.tables_data.extend(self.parse_yml_file(yml_file))
        self.tables_data.sort(
            key=lambda x: (x["name"].startswith("_"), x["name"].lstrip("_").lower())
        )
        print(f"Processed {len(self.tables_data)} tables")

    def generate_quarto_markdown(self) -> str:
        """Generate Quarto markdown content."""
        lines = [
            "---",
            f'title: "{self.config["title"]}"',
            f'description: "{self.config["description"]}"',
            # The project sets `toc: true`, which on this page means Pandoc builds
            # a right-hand list of all 43 table headings -- the same list the
            # generated sidebar block already puts on the left. One copy is enough,
            # and the sidebar is the one that stays visible across pages.
            "toc: false",
            "---",
            "",
            "## Overview",
            "",
            self.config["overview"],
            "",
            f"**Total Tables:** {len(self.tables_data)}",
            "",
            "---",
            "",
        ]
        for table in self.tables_data:
            lines.extend(self._generate_table_section(table))
        return "\n".join(lines)

    def _generate_table_section(self, table: Dict[str, Any]) -> List[str]:
        """Generate markdown section for a single table."""
        anchor = table_anchor(table["name"])
        heading = table_heading(table["name"])
        lines = [
            f"## {heading} {{#{anchor}}}",
            "",
        ]
        bq_location = self.bq_locations.get(table["name"])
        if bq_location:
            lines.extend([f"**BigQuery:** `{bq_location}`", ""])
        if table["description"]:
            lines.extend([markdown_prose(table["description"]), ""])
        for column in table["columns"]:
            lines.extend(self._generate_column_item(column))
        lines.extend(["---", ""])
        return lines

    def _generate_column_item(self, column: Dict[str, Any]) -> List[str]:
        """Generate collapsible details element for a column."""
        summary = f"**`{html.escape(column['name'])}`**"
        if column["data_type"]:
            summary += f" *({html.escape(column['data_type'])})*"

        lines = [
            "<details>",
            f"<summary>{summary}</summary>",
            "",
        ]
        if column["description"]:
            lines.extend([markdown_prose(column["description"]), ""])
        if column["constraints"]:
            lines.extend(["**Constraints:**", ""])
            for constraint in column["constraints"]:
                lines.append(f"- {constraint}")
            lines.append("")
        lines.extend(["</details>", ""])
        return lines

    def write_output(self, content: str):
        """Write generated content to output file."""
        output_path = self.project_root / self.config["output_file"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Documentation written to {output_path}")

    def sync_sidebar(self) -> bool:
        """Rewrite the per-table sidebar entries in ``docs/_quarto.yml``.

        The page body is regenerated on every render, but the sidebar used to be a
        hand-typed list of tables. It was written in the initial commit and never
        updated, so by the time dbt had grown to 43 baseline models the navigation
        still offered 30 — thirteen tables reachable only by scrolling or search.

        Rewriting it here from ``self.tables_data`` — the same parsed models the page
        itself is built from — makes that class of drift impossible. The edit is
        textual rather than a YAML round-trip because ``_quarto.yml`` is a
        hand-maintained file: a dump would discard its comments and reflow every
        unrelated block.

        Returns True if the file changed.
        """
        config_path = self.project_root / "docs" / "_quarto.yml"
        if not config_path.exists():
            print(f"Warning: {config_path} not found — sidebar not synced")
            return False

        page = Path(self.config["output_file"]).name
        lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)

        # The block to replace is the contiguous run of two-line entries
        #     - text: "<table>"
        #       href: <page>#<anchor>
        # A pair is identified by its href, so an entry pointing at the page itself
        # (the parent `section:`) is left alone.
        href_marker = f"href: {page}#"
        pairs = [
            i - 1
            for i, line in enumerate(lines)
            if i and href_marker in line and line.strip().startswith("href:")
            and lines[i - 1].strip().startswith("- text:")
        ]
        if not pairs:
            print(f"Warning: no '{href_marker}…' entries in {config_path.name}")
            return False

        start, end = pairs[0], pairs[-1] + 2
        indent = " " * (len(lines[start]) - len(lines[start].lstrip()))
        block: List[str] = []
        for table in self.tables_data:
            block.append(f'{indent}- text: "{table["name"]}"\n')
            block.append(f"{indent}  href: {page}#{table_anchor(table['name'])}\n")

        if lines[start:end] == block:
            print(f"Sidebar already lists all {len(block) // 2} tables")
            return False

        config_path.write_text(
            "".join(lines[:start] + block + lines[end:]), encoding="utf-8"
        )
        print(
            f"Sidebar synced in {config_path.name}: "
            f"{(end - start) // 2} entries -> {len(block) // 2}"
        )
        return True

    def cleanup(self):
        """Clean up temporary directory."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
            print("Cleaned up temporary files")

    def run(self):
        """Execute the full documentation generation process."""
        try:
            print("=" * 60)
            print(f"Generating: {self.config['title']}")
            print("=" * 60)

            repo_path = self.clone_repository()
            self.parse_manifest(repo_path)
            self.process_all_files(repo_path / self.config["yml_path"])

            print("Generating Quarto markdown...")
            content = self.generate_quarto_markdown()
            self.write_output(content)
            self.sync_sidebar()

            print("=" * 60)
            print("Documentation generation complete!")
            print("=" * 60)
        except Exception as e:
            print(f"Error during documentation generation: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        finally:
            self.cleanup()


def main():
    parser = argparse.ArgumentParser(
        description="Generate STARR-OMOP documentation from dbt YML files"
    )
    parser.add_argument(
        "model",
        choices=["omop"],
        help="Which data model to generate docs for",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    generator = DocGenerator(project_root, MODEL_CONFIGS[args.model])
    generator.run()


if __name__ == "__main__":
    main()
