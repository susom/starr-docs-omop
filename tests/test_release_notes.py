"""What the release-notes generator must refuse, and what it must guarantee.

Entries are published as written, and the page they build is public, so the
generator is the only check between an entry and the site.
These tests pin what it refuses -- structure that breaks the page, template
guidance left in, example values that look like real identifiers -- and what it
guarantees: newest release first, releases grouped under their year with an
index by month, one stable anchor per release, a table of contents of years
and months, the hooks the page's script and styles find it by, and identical
bytes for identical entries. The last test follows the page into llms.txt,
which is built from the same render list.

Run with pytest, or directly::

    python tests/test_release_notes.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import generate_llms_txt as gl  # noqa: E402
import generate_release_notes as rn  # noqa: E402

DOCS = Path(__file__).resolve().parents[1] / "docs"

CHANGE = """\
### Notes now count toward observation periods {#star-11694}

**What changed.** A person whose only record is a note now has an
`observation_period` row.

**Action required.** Re-pull `observation_period` if your work depends on it.
"""


def entry(summary="Notes now count toward observation periods.", body=CHANGE, extra=""):
    return f'---\nsummary: "{summary}"\n{extra}---\n\n{body}'


def load(files):
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for name, text in files.items():
            (folder / name).write_text(text, encoding="utf-8")
        return rn.load_entries(folder)


def problems_for(text, name="2026-10-15.qmd"):
    return load({name: text})[1]


def front_matter(page):
    return rn.yaml.safe_load(page.split("---\n")[1])


def test_newest_release_comes_first():
    entries, problems = load(
        {
            "2026-01-18.qmd": entry(body="### January change\n"),
            "2026-10-15.qmd": entry(body="### October change\n"),
            "2026-06-08.qmd": entry(body="### June change\n"),
        }
    )
    assert not problems, problems
    page = rn.render_page(entries)
    order = ["2026-10-15", "2026-06-08", "2026-01-18"]
    positions = [page.index(f"{{#release-{date}}}") for date in order]
    assert positions == sorted(positions), positions


def test_release_heading_carries_anchor_title_and_snapshot_suffix():
    page = rn.render_page(load({"2026-06-08.qmd": entry()})[0])
    assert "## June 2026 {#release-2026-06-08}" in page
    assert "Snapshot cut on **June 8, 2026** (`_2026_06_08`)" in page


def test_releases_are_grouped_under_their_year():
    entries, problems = load(
        {
            "2025-12-31.qmd": entry(body="### December change {#star-1}\n"),
            "2026-01-18.qmd": entry(body="### January change {#star-2}\n"),
            "2026-06-08.qmd": entry(body="### June change {#star-3}\n"),
        }
    )
    assert not problems, problems
    page = rn.render_page(entries)
    order = [
        "\n# 2026 {#year-2026}\n",
        "\n## June 2026 {#release-2026-06-08}\n",
        "\n## January 2026 {#release-2026-01-18}\n",
        "\n# 2025 {#year-2025}\n",
        "\n## December 2025 {#release-2025-12-31}\n",
    ]
    positions = [page.find(heading) for heading in order]
    assert -1 not in positions and positions == sorted(positions), positions


def test_the_index_links_every_release_by_year_and_month():
    entries = load(
        {
            "2025-12-31.qmd": entry(body="### December change {#star-1}\n"),
            "2026-01-18.qmd": entry(body="### January change {#star-2}\n"),
            "2026-06-08.qmd": entry(body="### June change {#star-3}\n"),
        }
    )[0]
    page = rn.render_page(entries)
    index = (
        "- **2026:** [June](#release-2026-06-08) \u00b7 [January](#release-2026-01-18)\n"
        "- **2025:** [December](#release-2025-12-31)\n"
    )
    assert index in page, page
    assert page.index(index) < page.index("# 2026 {#year-2026}")


def test_two_releases_in_one_month_are_told_apart_in_the_index():
    entries = load(
        {
            "2026-06-08.qmd": entry(body="### First {#star-1}\n"),
            "2026-06-22.qmd": entry(body="### Hotfix {#star-2}\n"),
            "2026-01-18.qmd": entry(body="### January {#star-3}\n"),
        }
    )[0]
    page = rn.render_page(entries)
    assert (
        "[June 22](#release-2026-06-22) \u00b7 [June 8](#release-2026-06-08) "
        "\u00b7 [January](#release-2026-01-18)"
    ) in page, page


def test_each_release_is_one_search_result():
    # Quarto's search has one result per `##` section, so releases must be the
    # only `##` headings: a change at that level would leave its month's result.
    files = {
        "2026-06-08.qmd": entry(body="### June {#star-1}\n\n#### Detail\n"),
        "2026-10-15.qmd": entry(body="### October {#star-2}\n"),
    }
    page = rn.render_page(load(files)[0])
    second_level = [line for line in page.split("\n") if line.startswith("## ")]
    assert second_level == [
        "## October 2026 {#release-2026-10-15}",
        "## June 2026 {#release-2026-06-08}",
    ], second_level


def test_the_table_of_contents_lists_every_year_and_month():
    # A change's heading is a whole sentence, so the table of contents stops at
    # the months, and keeps every one of them in view.
    meta = front_matter(rn.render_page(load({"2026-06-08.qmd": entry()})[0]))
    assert meta["toc-depth"] == 2 and meta["toc-expand"] == 2, meta


def test_the_page_loads_its_script_and_its_styles():
    # Only the front matter ties the page to its script and to its rules in
    # styles.css. Renaming either would leave every month open and every
    # heading full size, and nothing else would fail.
    meta = front_matter(rn.render_page(load({"2026-06-08.qmd": entry()})[0]))
    assert meta["body-classes"] == rn.PAGE_CLASS, meta
    tag = f'<script defer src="{rn.PAGE_SCRIPT}"></script>'
    assert tag in meta["include-in-header"]["text"], meta
    assert (DOCS / rn.PAGE_SCRIPT).is_file()
    styles = (DOCS / "styles.css").read_text(encoding="utf-8")
    assert f".{rn.PAGE_CLASS} " in styles


def test_the_script_finds_the_sections_the_generator_writes():
    script = (DOCS / rn.PAGE_SCRIPT).read_text(encoding="utf-8")
    for expected in (
        f'"{rn.PAGE_CLASS}"',
        f"section.level1[id^='{rn.YEAR_ANCHOR_PREFIX}']",
        f"section.level2[id^='{rn.RELEASE_ANCHOR_PREFIX}']",
    ):
        assert expected in script, expected
    page = rn.render_page(load({"2026-06-08.qmd": entry()})[0])
    assert f"{{#{rn.YEAR_ANCHOR_PREFIX}2026}}" in page
    assert f"{{#{rn.RELEASE_ANCHOR_PREFIX}2026-06-08}}" in page


def test_title_can_be_overridden():
    files = {"2026-06-08.qmd": entry(extra='title: "June 2026 (hotfix)"\n')}
    assert "## June 2026 (hotfix) {#release-2026-06-08}" in rn.render_page(
        load(files)[0]
    )


def test_identical_entries_render_identical_bytes():
    files = {"2026-06-08.qmd": entry(), "2026-10-15.qmd": entry(body="### Other\n")}
    assert rn.render_page(load(files)[0]) == rn.render_page(load(files)[0])


def test_the_page_passes_the_whitespace_hooks():
    # pre-commit's trailing-whitespace and end-of-file-fixer would rewrite a
    # generated file on every commit, so it must leave nothing for them to do.
    body = "### Change   \n\nText.\t\n   \n\n"
    page = rn.render_page(load({"2026-06-08.qmd": entry(body=body)})[0])
    assert page.endswith("Text.\n") and not page.endswith("\n\n"), repr(page[-20:])
    assert all(line == line.rstrip() for line in page.split("\n")), page


def test_template_and_readme_are_not_entries():
    entries, problems = load(
        {
            "_template.qmd": "<!-- guidance -->\n## anything\n",
            "README.md": "# How to write an entry\n",
            "2026-06-08.qmd": entry(),
        }
    )
    assert not problems, problems
    assert [e.date.isoformat() for e in entries] == ["2026-06-08"]


def test_an_entry_named_anything_but_its_date_is_refused():
    # Skipping it instead would silently drop a whole release from the page.
    assert problems_for(entry(), name="october-2026.qmd")
    assert problems_for(entry(), name="2026-13-01.qmd")


def test_front_matter_and_summary_are_optional():
    # An entry can be just the changes, as the pull requests describe them.
    entries, problems = load({"2026-10-15.qmd": CHANGE})
    assert not problems, problems
    page = rn.render_page(entries)
    assert "(released_datasets.qmd).\n\n### Notes now count" in page, page


def test_a_given_summary_or_title_must_be_text():
    assert problems_for(entry(summary=""))
    assert problems_for(entry(extra="title:\n"))
    assert problems_for("---\nsummary: The block is never closed.\n\n" + CHANGE)


def test_user_impact_text_passes_as_written():
    # The layout of a starr-data-lake pull request's User Impact section.
    body = (
        "### Notes now count toward observation periods {#star-11694}\n\n"
        "**What changed**\n\nThe `note` table now counts.\n\n"
        "**Who is affected**\n\n- STARR OMOP 5.4 — `observation_period` users\n\n"
        "**Action required**\n\nNone.\n\n"
        "**Example (illustrative, PHI-free)**\n\n"
        "| person_id | observation_period_start_date |\n"
        "| --- | --- |\n"
        "| 1001 | 2020-03-15 |\n"
    )
    assert not problems_for(body)


def test_a_misspelled_front_matter_key_is_refused():
    problems = problems_for(entry(extra="sumary: typo\n"))
    assert any("sumary" in problem for problem in problems), problems


def test_changes_must_nest_under_the_release_heading():
    problems = problems_for(entry(body="## Too high\n\nText.\n"))
    assert any(":5:" in p and "###" in p for p in problems), problems


def test_a_heading_inside_a_code_block_is_code():
    body = "### Counting people\n\n```python\n# one row per person\nprint(1)\n```\n"
    assert not problems_for(entry(body=body))


def test_template_guidance_left_in_is_refused():
    assert problems_for(entry(body="### Change\n\n<!-- What changed? -->\n"))
    assert problems_for(entry(summary="<!-- One sentence. -->"))


def test_identifier_shaped_example_values_are_refused():
    assert problems_for(entry(body="### Change\n\n| MRN1234 | 1980-01-01 |\n"))
    assert problems_for(entry(body="### Change\n\n```\nssn = 123-45-6789\n```\n"))
    assert not problems_for(
        entry(body="### Change\n\n| person_id |\n|---|\n| 1001 |\n")
    )


def test_anchors_are_unique_across_the_page():
    # Both entries carry CHANGE, and with it {#star-11694}.
    problems = load({"2026-06-08.qmd": entry(), "2026-10-15.qmd": entry()})[1]
    assert any("#star-11694" in problem for problem in problems), problems


def test_a_year_heading_anchor_cannot_be_reused():
    problems = problems_for(entry(body="### Change {#year-2026}\n"))
    assert any("#year-2026" in problem for problem in problems), problems


def test_a_release_heading_anchor_cannot_be_reused():
    problems = load(
        {
            "2026-06-08.qmd": entry(body="### June {#star-1}\n"),
            "2026-10-15.qmd": entry(body="### October {#release-2026-06-08}\n"),
        }
    )[1]
    assert any("#release-2026-06-08" in problem for problem in problems), problems


def test_no_entries_still_builds_a_page():
    entries, problems = load({})
    assert not problems, problems
    page = rn.render_page(entries)
    assert page.startswith('---\ntitle: "Release Notes"\n')
    assert rn.NO_ENTRIES in page
    assert "\n# " not in page and "- **" not in page, page


def test_a_release_missing_from_released_datasets_is_flagged():
    entries = load({"2026-10-15.qmd": entry()})[0]
    assert rn.snapshot_warnings(entries, "starr_omop_cdm54_confidential_2026_06_08")
    assert not rn.snapshot_warnings(entries, "starr_omop_cdm54_confidential_2026_10_15")


def test_the_page_reaches_llms_txt():
    entries = load({"2026-10-15.qmd": entry()})[0]
    config = {
        "website": {"title": "STARR-OMOP Docs"},
        "project": {"render": [rn.OUTPUT_FILE]},
    }
    with tempfile.TemporaryDirectory() as tmp:
        docs = Path(tmp)
        (docs / rn.OUTPUT_FILE).write_text(rn.render_page(entries), encoding="utf-8")
        index = gl.generate_llms_txt(config, docs, "https://example.org/about/")
        full = gl.generate_llms_full_txt(config, docs)
    assert "[Release Notes](https://example.org/about/release_notes.html)" in index
    assert "Notes now count toward observation periods" in full
    assert "{#" not in full
    assert "<script" not in full


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
