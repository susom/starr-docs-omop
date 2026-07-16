#!/usr/bin/env python3
"""
Documentation generator for STARR-OMOP dbt models.

Generates Quarto markdown documentation from dbt YML files for the
STARR-OMOP data model.

Usage:
    python generate_docs.py omop

Note: Activate the virtual environment before running:
    source .venv/bin/activate
"""

import argparse
import html
import json
import os
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
        # Anchor id strips leading underscores so Quarto section links resolve.
        anchor = table["name"].lstrip("_")
        # Escape leading underscores in the heading text so they render literally.
        heading = table["name"].upper().lstrip("_")
        heading = "\\_" * (len(table["name"]) - len(table["name"].lstrip("_"))) + heading
        lines = [
            f"## {heading} {{#{anchor}}}",
            "",
        ]
        bq_location = self.bq_locations.get(table["name"])
        if bq_location:
            lines.extend([f"**BigQuery:** `{bq_location}`", ""])
        if table["description"]:
            lines.extend([table["description"], ""])
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
            lines.extend([column["description"], ""])
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
