#!/usr/bin/env python3
"""
Generate the Release Notes page from the entries in docs/release_notes/.

Every STARR-OMOP dataset release has one entry, ``docs/release_notes/YYYY-MM-DD.qmd``,
named for the date that suffixes its dataset names (2026-09-10 -> ``_2026_09_10``),
so an entry and a release on the Released Datasets page share one key. The
date of the release's section in starr-data-lake's ``CHANGELOG.adoc`` can be a
few days off: the section for ``_2026_09_10`` is dated 2026-09-11.

An entry holds, as written, the User Impact sections of the starr-data-lake
pull requests whose changelog entries carry the ``STARR OMOP 5.4 Docs`` tag
(STAR-12576). Nothing here fetches them: pull request text can change after
merge, and the changelog fold deletes the tagged fragments. The hook only
checks the committed entries and assembles them into ``docs/release_notes.qmd``,
newest first and grouped by year and month::

    # 2026                  one heading per year
    ## September 2026       one per release, titled with its month and year
    ### <change>            the entry's own sections

Quarto's search has one result per ``##`` section, so a search hit names the
month a change shipped in. An index under the introduction links every
release, by year and month, and the table of contents lists the years and
months. The page's script, ``docs/assets/release-notes.js``, makes each year and
month open and close, with the newest open; without it every section is open.
The hook reads no network and no clock, so unchanged entries always produce
identical bytes.

Usage:
    python scripts/generate_release_notes.py          # check, then write the page
    python scripts/generate_release_notes.py --check  # check only

A failed check exits non-zero, which stops ``quarto render`` and ``quarto
publish`` before an entry that breaks the rules can reach the site.
"""

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

try:
    import yaml
except ImportError:
    print("Error: PyYAML not found.")
    print("Please activate the virtual environment: source .venv/bin/activate")
    sys.exit(1)


ENTRIES_DIR = "release_notes"
OUTPUT_FILE = "release_notes.qmd"
RELEASED_DATASETS = "released_datasets.qmd"

ENTRY_NAME = re.compile(r"\A(\d{4}-\d{2}-\d{2})\.qmd\Z")
FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)(.*)\Z", re.DOTALL)
FRONT_MATTER_KEYS = ("summary", "title")

# Spelled out rather than taken from strftime, whose month names follow the
# locale of whoever happens to run `quarto publish`.
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# docs/assets/release-notes.js finds the year and release sections by these
# prefixes; tests/test_release_notes.py checks that the two still agree.
YEAR_ANCHOR_PREFIX = "year-"
RELEASE_ANCHOR_PREFIX = "release-"
# Raw HTML, so Quarto leaves the path as written: relative to the page, which
# renders at the root of the site.
PAGE_SCRIPT = "assets/release-notes.js"
PAGE_CLASS = "release-notes"

PAGE_HEADER = (
    "---\n"
    'title: "Release Notes"\n'
    'description: "What changed for users in each STARR-OMOP v5.4 dataset release"\n'
    # Scopes the page's rules in styles.css, and its script, to this page.
    f"body-classes: {PAGE_CLASS}\n"
    # Years and months only: a change's heading is a whole sentence, too long
    # for the sidebar, and its month already leads to it.
    "toc-depth: 2\n"
    # Quarto opens only the top level of the table of contents (the years) by
    # default; 2 keeps every month in view too.
    "toc-expand: 2\n"
    "include-in-header:\n"
    "  text: |\n"
    f'    <script defer src="{PAGE_SCRIPT}"></script>\n'
    "---\n"
    "\n"
    "What changed for users in each STARR-OMOP v5.4 dataset release, newest first "
    "and grouped by year and month. Each release is a dated, immutable snapshot; "
    f"[Released Datasets]({RELEASED_DATASETS}) lists its dataset names. "
    "To find the releases that changed a table or field, search the site for its "
    "name.\n"
)
NO_ENTRIES = "No release notes have been published yet.\n"
INDEX_SEPARATOR = " \u00b7 "

# The page gives each year a `#` heading and each release a `##` heading, so the
# changes inside an entry start at `###`. A `#` or `##` in an entry would
# promote one change to the level of a whole year or release in the table of
# contents, and split the release in Quarto's search, which has one result per
# `##` section. Indented ATX headings (up to 3 leading spaces in Markdown) and
# Setext underlines (`=` or `-` under text) also promote changes to `#` or `##`.
RELEASE_LEVEL_HEADING = re.compile(r"\A[ ]{0,3}#{1,2}(?:\s|\Z)")
SETEXT_UNDERLINE = re.compile(r"\A[ ]{0,3}(=+|-+)[ \t]*\Z")
FENCE = re.compile(r"\A\s*(```|~~~)")
EXPLICIT_ID = re.compile(r"\{#([^}\s]+)")

# Entries are published as written, so these are a backstop, not a review: an
# example value that looks like a real identifier stops the render. They apply
# inside code blocks too, where example rows are often pasted.
IDENTIFIER_PATTERNS = (
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "an SSN-shaped number"),
    (
        re.compile(r"\bSSN\s*[:#]?\s*\d{9}\b", re.IGNORECASE),
        "an SSN followed by digits",
    ),
    (re.compile(r"\bMRN\s*[:#]?\s*\d", re.IGNORECASE), "an MRN followed by digits"),
)


@dataclass(frozen=True)
class Entry:
    date: dt.date
    title: str
    summary: str
    body: str

    @property
    def anchor(self) -> str:
        return f"{RELEASE_ANCHOR_PREFIX}{self.date.isoformat()}"

    @property
    def suffix(self) -> str:
        """Dataset-name suffix of the snapshot: 2026-06-08 -> ``_2026_06_08``."""
        return "_" + self.date.isoformat().replace("-", "_")


def long_date(date: dt.date) -> str:
    return f"{MONTHS[date.month - 1]} {date.day}, {date.year}"


def year_anchor(year: int) -> str:
    return f"{YEAR_ANCHOR_PREFIX}{year}"


def by_year(entries: List[Entry]) -> List[Tuple[int, List[Entry]]]:
    """Entries grouped by the year of their snapshot, keeping their order."""
    return [
        (year, list(group))
        for year, group in groupby(entries, key=lambda entry: entry.date.year)
    ]


def numbered_lines(body: str, first_line: int) -> Iterator[Tuple[int, str, bool]]:
    """Each line of an entry body with its line number in the file.

    The flag is True inside a fenced code block, where a ``##`` is a comment in
    the code being shown rather than a heading on the page.
    """
    fenced = False
    for offset, line in enumerate(body.split("\n")):
        if FENCE.match(line):
            fenced = not fenced
            yield first_line + offset, line, True
            continue
        yield first_line + offset, line, fenced


def identifier_problems(where: str, text: str) -> List[str]:
    return [
        f"{where}: {label}; example values must be synthetic and must not look real"
        for pattern, label in IDENTIFIER_PATTERNS
        if pattern.search(text)
    ]


def check_entry(name: str, text: str) -> Tuple[Optional[Entry], List[str]]:
    """Parse one entry file, returning it or every reason it cannot be published."""
    named = ENTRY_NAME.match(name)
    if not named:
        return None, [f"{name}: entries are named YYYY-MM-DD.qmd, the snapshot date"]
    try:
        date = dt.date.fromisoformat(named.group(1))
    except ValueError:
        return None, [f"{name}: {named.group(1)} is not a calendar date"]

    text = text.replace("\r\n", "\n")
    # Front matter is optional, so an entry can be just the changes, exactly as
    # the starr-data-lake pull requests describe them.
    meta: Dict[str, object] = {}
    body, first_line = text, 1
    if text.startswith("---"):
        parsed = FRONT_MATTER.match(text)
        if not parsed:
            return None, [f"{name}: the front matter block is not closed with ---"]
        try:
            meta = yaml.safe_load(parsed.group(1)) or {}
        except yaml.YAMLError as error:
            return None, [f"{name}: front matter is not valid YAML ({error})"]
        if not isinstance(meta, dict):
            return None, [f"{name}: front matter must be a set of `key: value` lines"]
        body = parsed.group(2)
        first_line = text.count("\n", 0, parsed.start(2)) + 1

    problems = []
    unknown = sorted(str(key) for key in meta if key not in FRONT_MATTER_KEYS)
    if unknown:
        problems.append(
            f"{name}: unknown front matter key(s) {', '.join(unknown)} "
            f"(allowed: {', '.join(FRONT_MATTER_KEYS)})"
        )

    summary = meta.get("summary", "")
    title = meta.get("title", f"{MONTHS[date.month - 1]} {date.year}")
    for field, value in (("summary", summary), ("title", title)):
        if not isinstance(value, str) or (field in meta and not value.strip()):
            problems.append(f"{name}: `{field}` must be text when it is given")
            continue
        if "<!--" in value:
            problems.append(f"{name}: `{field}` still holds template guidance")
        problems.extend(identifier_problems(f"{name} ({field})", value))

    if isinstance(title, str) and ("\n" in title or "\r" in title):
        problems.append(f"{name}: `title` must be a single line")

    prev_line = ""
    for number, line, fenced in numbered_lines(body, first_line):
        where = f"{name}:{number}"
        if not fenced:
            if RELEASE_LEVEL_HEADING.match(line):
                problems.append(
                    f"{where}: start each change at `###`; `#` and `##` belong to the page"
                )
            elif (
                SETEXT_UNDERLINE.match(line)
                and prev_line.strip()
                and not prev_line.lstrip().startswith(("#", "<!--", "|"))
            ):
                problems.append(
                    f"{where}: start each change at `###`; `#` and `##` belong to the page"
                )
        if not fenced and "<!--" in line:
            problems.append(f"{where}: template guidance left in (an HTML comment)")
        problems.extend(identifier_problems(where, line))
        prev_line = "" if fenced else line
    if not body.strip():
        problems.append(f"{name}: no changes; add at least one `###` section")

    if problems:
        return None, problems
    tidy_body = "\n".join(line.rstrip() for line in body.split("\n")).strip("\n")
    return Entry(date, title.strip(), " ".join(summary.split()), tidy_body), []


def anchor_problems(entries: List[Entry]) -> List[str]:
    """Explicit ``{#id}`` anchors must be unique across the whole page."""
    owner: Dict[str, str] = {entry.anchor: f"{entry.date}.qmd" for entry in entries}
    for entry in entries:
        owner[year_anchor(entry.date.year)] = f"the page's {entry.date.year} heading"
    problems = []
    for entry in entries:
        for ident in EXPLICIT_ID.findall(entry.body):
            if ident in owner:
                problems.append(
                    f"{entry.date}.qmd: anchor #{ident} is already used by {owner[ident]}"
                )
            else:
                owner[ident] = f"{entry.date}.qmd"
    return problems


def load_entries(entries_dir: Path) -> Tuple[List[Entry], List[str]]:
    """Every publishable entry, newest first, and every problem found."""
    entries: List[Entry] = []
    problems: List[str] = []
    if entries_dir.is_dir():
        for path in sorted(entries_dir.glob("*.qmd")):
            # `_template.qmd`, and anything else Quarto itself would skip.
            if path.name.startswith(("_", ".")):
                continue
            entry, found = check_entry(path.name, path.read_text(encoding="utf-8"))
            problems.extend(found)
            if entry:
                entries.append(entry)
    entries.sort(key=lambda entry: entry.date, reverse=True)
    problems.extend(anchor_problems(entries))
    return entries, problems


def snapshot_warnings(entries: List[Entry], released_datasets: str) -> List[str]:
    """Releases with notes but no dataset on the hand-maintained datasets page."""
    return [
        f"{entry.date}.qmd: no `{entry.suffix}` dataset on {RELEASED_DATASETS} yet"
        for entry in entries
        if entry.suffix not in released_datasets
    ]


def render_release(entry: Entry) -> str:
    lines = [
        f"## {entry.title} {{#{entry.anchor}}}",
        "",
        f"Snapshot cut on **{long_date(entry.date)}** (`{entry.suffix}`). "
        f"Dataset names are on [Released Datasets]({RELEASED_DATASETS}).",
        "",
    ]
    if entry.summary:
        lines += [entry.summary, ""]
    return "\n".join(lines + [entry.body, ""])


def index_label(entry: Entry, same_year: List[Entry]) -> str:
    """The release's month, with the day when another release shares the month."""
    month = MONTHS[entry.date.month - 1]
    if sum(other.date.month == entry.date.month for other in same_year) > 1:
        return f"{month} {entry.date.day}"
    return month


def render_index(entries: List[Entry]) -> str:
    """One line per year, linking each of its releases by month."""
    lines = []
    for year, releases in by_year(entries):
        links = INDEX_SEPARATOR.join(
            f"[{index_label(entry, releases)}](#{entry.anchor})" for entry in releases
        )
        lines.append(f"- **{year}:** {links}")
    return "\n".join(lines) + "\n"


def render_page(entries: List[Entry]) -> str:
    """The whole page: newest first, a `#` heading per year, a `##` per release."""
    if not entries:
        return PAGE_HEADER + "\n" + NO_ENTRIES
    blocks = [render_index(entries)]
    for year, releases in by_year(entries):
        blocks.append(f"# {year} {{#{year_anchor(year)}}}\n")
        blocks.extend(render_release(entry) for entry in releases)
    return PAGE_HEADER + "\n" + "\n".join(blocks)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build docs/release_notes.qmd from docs/release_notes/*.qmd"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the entries without writing the page",
    )
    args = parser.parse_args(argv)

    docs_dir = Path(__file__).resolve().parent.parent / "docs"
    entries, problems = load_entries(docs_dir / ENTRIES_DIR)
    if problems:
        print(
            "Release notes cannot be published until these are fixed:", file=sys.stderr
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    datasets_page = docs_dir / RELEASED_DATASETS
    datasets = (
        datasets_page.read_text(encoding="utf-8") if datasets_page.exists() else ""
    )
    for warning in snapshot_warnings(entries, datasets):
        print(f"Warning: {warning}")

    if args.check:
        print(f"Release notes: {len(entries)} entries pass the checks")
        return 0
    (docs_dir / OUTPUT_FILE).write_text(render_page(entries), encoding="utf-8")
    print(f"Generated {OUTPUT_FILE} from {len(entries)} release entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
