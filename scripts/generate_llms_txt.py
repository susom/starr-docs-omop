#!/usr/bin/env python3
"""
Generates llms.txt and llms-full.txt from Quarto documentation.

Reads _quarto.yml for site structure and QMD files for content,
producing:
  - llms.txt   — concise site summary with page links (llmstxt.org spec)
  - llms-full.txt — full plaintext content for LLM context windows

Usage:
    python generate_llms_txt.py --base-url https://example.github.io/

Runs as a Quarto pre-render script (after generate_docs.py).
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML not found.")
    print("Please activate the virtual environment: source .venv/bin/activate")
    sys.exit(1)


def parse_quarto_config(docs_dir):
    with open(docs_dir / "_quarto.yml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def extract_qmd(qmd_path):
    text = qmd_path.read_text(encoding="utf-8")
    frontmatter = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()
    return {
        "title": frontmatter.get("title", ""),
        "description": frontmatter.get("description", ""),
        "body": body,
    }


def strip_to_plaintext(text):
    text = re.sub(r"<details>\s*", "", text)
    text = re.sub(r"</details>\s*", "", text)
    text = re.sub(r"<summary>(.*?)</summary>", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r":::\s*\{[^}]*\}", "", text)
    text = re.sub(r":::", "", text)
    text = re.sub(r"\{#[^}]+\}", "", text)
    text = re.sub(r"\{\.[^}]+\}", "", text)
    text = re.sub(r"```\{python\}.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"```\{r\}.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*`([^`]+)`\*\*", r"\1", text)
    text = re.sub(r"\*\(([^)]+)\)\*", r"(\1)", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_tables_summary(body):
    tables = []
    current_table = None
    columns = []

    for line in body.split("\n"):
        match = re.match(r"^##\s+(\S+)", line)
        if match and not line.strip().startswith("## Overview"):
            if current_table:
                tables.append({"name": current_table, "columns": columns})
            current_table = match.group(1)
            columns = []
            continue

        col_match = re.search(r"\*\*`([^`]+)`\*\*", line)
        if col_match and current_table:
            columns.append(col_match.group(1))

    if current_table:
        tables.append({"name": current_table, "columns": columns})

    return tables


def generate_llms_txt(config, docs_dir, base_url):
    site_title = config.get("website", {}).get("title", "Documentation")
    pages = config.get("project", {}).get("render", [])

    lines = [f"# {site_title}", ""]

    for page in pages:
        qmd_path = docs_dir / page
        if not qmd_path.exists():
            continue
        content = extract_qmd(qmd_path)
        if content["description"] and ("starr_" in page or "index" in page):
            lines.append(f"> {content['description']}")
            lines.append("")
            break

    lines.extend(["## Pages", ""])

    for page in pages:
        qmd_path = docs_dir / page
        if not qmd_path.exists():
            continue
        content = extract_qmd(qmd_path)
        html_page = page.replace(".qmd", ".html")
        url = f"{base_url.rstrip('/')}/{html_page}" if base_url else html_page
        title = content["title"] or page
        desc = content["description"] or ""
        if desc:
            lines.append(f"- [{title}]({url}): {desc}")
        else:
            lines.append(f"- [{title}]({url})")

    data_model_pages = [p for p in pages if "data_model" in p]
    if data_model_pages:
        lines.extend(["", "## Data Model Tables", ""])
        for page in data_model_pages:
            qmd_path = docs_dir / page
            if not qmd_path.exists():
                continue
            content = extract_qmd(qmd_path)
            tables = extract_tables_summary(content["body"])
            for table in tables:
                html_page = page.replace(".qmd", ".html")
                anchor = table["name"].lower().lstrip("_")
                url = f"{base_url.rstrip('/')}/{html_page}#{anchor}" if base_url else f"{html_page}#{anchor}"
                col_count = len(table["columns"])
                lines.append(f"- [{table['name']}]({url}): {col_count} columns")

    lines.append("")
    return "\n".join(lines)


def generate_llms_full_txt(config, docs_dir):
    site_title = config.get("website", {}).get("title", "Documentation")
    pages = config.get("project", {}).get("render", [])

    lines = [f"# {site_title}", ""]

    for page in pages:
        qmd_path = docs_dir / page
        if not qmd_path.exists():
            continue

        content = extract_qmd(qmd_path)
        plain_body = strip_to_plaintext(content["body"])

        lines.append("=" * 60)
        if content["title"]:
            lines.append(f"# {content['title']}")
        if content["description"]:
            lines.append(content["description"])
        lines.append("=" * 60)
        lines.append("")
        lines.append(plain_body)
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate llms.txt and llms-full.txt from Quarto documentation"
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="Base URL for the deployed site (used in llms.txt links)",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    docs_dir = project_root / "docs"

    print("Generating llms.txt and llms-full.txt...")

    config = parse_quarto_config(docs_dir)

    llms_txt = generate_llms_txt(config, docs_dir, args.base_url)
    llms_txt_path = docs_dir / "llms.txt"
    llms_txt_path.write_text(llms_txt, encoding="utf-8")

    llms_full = generate_llms_full_txt(config, docs_dir)
    llms_full_path = docs_dir / "llms-full.txt"
    llms_full_path.write_text(llms_full, encoding="utf-8")

    print(f"  llms.txt:      {len(llms_txt):,} chars → {llms_txt_path}")
    print(f"  llms-full.txt: {len(llms_full):,} chars → {llms_full_path}")


if __name__ == "__main__":
    main()
