"""What the release-notes generator must refuse, and what it must guarantee.

Entries are reviewed with their source changes and published as written. The
generator adds structural and identifier checks before they reach the public site.
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

import re
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


def test_title_must_be_single_line():
    problems = problems_for(entry(extra='title: "June 2026\\n# Extra"\n'))
    assert any("title" in p and "single line" in p for p in problems), problems


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
    # Indented ATX headings up to 3 spaces must also be refused
    assert any("###" in p for p in problems_for(entry(body="  ## Indented\n\nText.\n")))
    assert any("###" in p for p in problems_for(entry(body="   # Level 1\n\nText.\n")))
    # Setext headings must also be refused
    assert any("###" in p for p in problems_for(entry(body="Title\n---\n\nText.\n")))
    assert any("###" in p for p in problems_for(entry(body="Title\n===\n\nText.\n")))
    # Thematic break preceded by a blank line is allowed
    assert not problems_for(entry(body="### Change\n\nText.\n\n---\n\nMore text.\n"))
    # A four-space-indented literal is not a fence, so a following heading is caught
    assert any(
        "###" in p for p in problems_for(entry(body="    ```\n## Escaped\n\nText.\n"))
    )
    # Mismatched fence delimiters do not close the block or allow headings to escape
    assert any(
        "###" in p
        for p in problems_for(entry(body="```\n~~~\n```\n## Escaped\n\nText.\n"))
    )
    assert any(
        "###" in p
        for p in problems_for(entry(body="~~~\n```\n~~~\n## Escaped\n\nText.\n"))
    )
    # A shorter closing fence does not close a longer opening fence
    assert any(
        "###" in p
        for p in problems_for(entry(body="````\n```\n````\n## Escaped\n\nText.\n"))
    )
    # Summary headings that break the page structure are also refused
    assert any("###" in p for p in problems_for(entry(summary="## Injected release")))
    assert any("###" in p for p in problems_for(entry(summary="# Injected year")))
    assert any("###" in p for p in problems_for(entry(summary="  ## Indented")))
    assert any("###" in p for p in problems_for(entry(summary="Title\\n===\\n")))
    assert any("###" in p for p in problems_for(entry(summary="Title\\n---\\n")))


def test_each_entry_requires_an_unfenced_level_three_heading():
    for body in (
        "",
        "A change described only in prose.\n",
        "#### Details without a change\n",
        "```markdown\n### Example, not a change\n```\n",
        "~~~markdown\n### Example, not a change\n~~~\n",
        "    ### Indented code, not a change\n",
        "###Not a heading\n",
        r"\### Escaped, not a heading",
    ):
        problems = problems_for(entry(summary="### Summary heading", body=body))
        assert any("at least one `###` section" in p for p in problems), body
    for indent in range(4):
        assert not problems_for(entry(body=" " * indent + "### Actual change\n"))


def test_a_heading_inside_a_code_block_is_code():
    body = "### Counting people\n\n```python\n# one row per person\nprint(1)\n```\n"
    assert not problems_for(entry(body=body))
    # Tilde fences also protect code comments
    tilde_body = "### Tildes\n\n~~~python\n# one row per person\nprint(1)\n~~~\n"
    assert not problems_for(entry(body=tilde_body))
    # Longer fences allow inner fences as literal code
    nested_body = "### Nested\n\n````markdown\n```python\n# comment\n```\n````\n"
    assert not problems_for(entry(body=nested_body))
    # Comments inside code fences in a summary are not treated as headings
    summary_code = "```python\n# comment\n```\nSummary sentence."
    assert not problems_for(entry(summary=summary_code))


def test_template_guidance_left_in_is_refused():
    assert problems_for(entry(body="### Change\n\n<!-- What changed? -->\n"))
    assert problems_for(entry(summary="<!-- One sentence. -->"))


def test_identifier_shaped_example_values_are_refused():
    assert problems_for(entry(body="### Change\n\n| MRN1234 | 1980-01-01 |\n"))
    assert problems_for(entry(body="### Change\n\n```\nssn = 123-45-6789\n```\n"))
    assert problems_for(entry(body="### Change\n\nSSN: 123456789\n"))
    assert problems_for(entry(body="### Change\n\n```\nssn 123456789\n```\n"))
    assert not problems_for(
        entry(body="### Change\n\n| person_id |\n|---|\n| 1001 |\n")
    )


def test_wrapped_and_formatted_identifiers_are_refused_with_source_locations():
    for value in (
        "MRN:\n1234567",
        "MRN: **1234567**",
        "**MRN:** `1234567`",
        "[MRN](https://example.org): **1234567**",
        "SSN:\n**123456789**",
        "SSN = **123456789**",
        "MRN = `1234567`",
        "SSN: 123**456**789",
        "MRN&colon;&nbsp;**1234567**",
        "| MRN: | **1234567** |",
        "| SSN | `123456789` |",
        "| MRN | Result |\n| --- | --- |\n| **1234567** | Positive |",
        "| Result | SSN |\n| --- | --- |\n| Positive | `123456789` |",
    ):
        problems = problems_for("### Change\n\n" + value + "\n")
        assert problems, value
        assert any(
            re.match(r"2026-10-15\.qmd:\d+:", problem) for problem in problems
        ), problems
        assert not any("1234567" in problem for problem in problems), problems
    assert problems_for(entry(summary="MRN: **1234567**"))
    assert problems_for(entry(body="### Change\n\n```text\nMRN:\n1234567\n```\n"))
    assert not problems_for(entry(body="### Change\n\nMRN: `EXAMPLE_MRN`\n"))


def test_identifier_locations_survive_markdown_normalization():
    for text, line in (
        ("Intro.\nMRN:\n**1234567**", 21),
        ("Intro.\nSSN: [123456789](https://example.org/\nmore)", 21),
        (
            "| Result | MRN |\n| --- | --- |\n| Positive | **1234567** |",
            22,
        ),
    ):
        problems = rn.identifier_problems("entry.qmd", text, first_line=20)
        assert len(problems) == 1, problems
        assert problems[0].startswith(f"entry.qmd:{line}:"), problems
        assert "1234567" not in problems[0], problems


def test_executable_or_private_note_content_is_refused():
    for value in (
        "<script>alert(1)</script>",
        "```{python}\nprint('not executed')\n```",
        "~~~{ruby}\nputs 'not executed'\n~~~",
        "{{< include private.txt >}}",
        "[Internal ticket](https://stanfordmed.atlassian.net/browse/STAR-1)",
    ):
        assert problems_for(entry(body="### Change\n\n" + value)), value


def change(value):
    return entry(body="### Change\n\n" + value + "\n")


def test_link_and_image_destinations_must_use_a_safe_scheme():
    for value in (
        "[click](javascript:alert(1))",
        "[click](JaVaScRiPt:alert(1))",
        '[click](javascript:alert(1) "title")',
        "[click](<javascript:alert(1)>)",
        "[click](vbscript:msgbox(1))",
        "[click](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
        "[click](file:///etc/passwd)",
        "[click](ftp://files.example.org/data.csv)",
        "![logo](javascript:alert(1))",
        "![logo](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)",
        "[click][ref]\n\n[ref]: javascript:alert(1)",
        "[ref]\n\n[ref]: <javascript:alert(1)>",
        "[ref]\n\n[ref]:\n    javascript:alert(1)",
        "> [click](javascript:alert(1))",
        "- [click](javascript:alert(1))",
        "> [ref]: javascript:alert(1)",
        "> [ref]:\n> javascript:alert(1)",
        ">> [ref]:\n>> JAVA&#x53;CRIPT:alert(1)",
        "> [click](\n> javascript:alert(1))",
        "**[click](javascript:alert(1))**",
        "[a](https://example.org)[b](javascript:alert(1))",
        "```\n[click](javascript:alert(1))\n```",
        "`[click](javascript:alert(1))`",
        "| a |\n| --- |\n| [click](javascript:alert(1)) |",
    ):
        problems = problems_for(change(value))
        assert any("link destination" in p for p in problems), (value, problems)


def test_the_scheme_check_reads_a_destination_the_way_a_browser_does():
    # Pandoc decodes entities and escapes in a destination and drops the spaces
    # around it, and a browser ignores spaces before a URL and tabs and
    # newlines inside it. Each of these is a live `javascript:` link.
    for value in (
        "[click](&#106;avascript:alert(1))",
        "[click](&#x6A;avascript:alert(1))",
        "[click](javascript&colon;alert(1))",
        "[click](javascript\\:alert(1))",
        "[click](java&#9;script:alert(1))",
        "[click](java&Tab;script:alert(1))",
        "[click](\tjavascript:alert(1))",
        "[click](\njavascript:alert(1))",
        "[click](   javascript:alert(1))",
        "[click](&#32;javascript:alert(1))",
        "[click](\u3000\njavascript:alert(1))",
        "[click](" + "&#32;" * 5000 + "javascript:alert(1))",
        "[click](\\ javascript:alert(1))",
        "[click](\\\tjavascript:alert(1))",
        "[click](\\\njavascript:alert(1))",
        "[click](\\\u3000javascript:alert(1))",
        "[click](&nbsp;javascript:alert(1))",
        "[click](\u00a0javascript:alert(1))",
        "[click](&nbsp;\u202fjavascript:alert(1))",
        "[click](&#11;javascript:alert(1))",
        "![click](&#32;  \n javascript:alert(1))",
    ):
        problems = problems_for(change(value))
        assert any("link destination" in p for p in problems), (value, problems)
    # The page keeps a summary on one line, so any Unicode space is a space.
    for space in ("\\u2028", "\\u3000", "\\x85", "\\n"):
        problems = problems_for(entry(summary=f"[click]({space}javascript:alert(1))"))
        assert any("link destination" in p for p in problems), (space, problems)


def test_ordinary_links_are_published_as_written():
    for value in (
        "[x](https://example.org/path?a=1&b=2#frag)",
        "[x](http://example.org)",
        "[x](mailto:starr@example.org)",
        "[x](HTTPS://EXAMPLE.ORG)",
        '[x](https://example.org "A title")',
        "[x](released_datasets.qmd#_2026_10_15)",
        "[x](#star-11694)",
        "[x](../tables/person.html)",
        "[x](//example.org/x)",
        "[x](notes/a:b.qmd)",
        "![diagram](diagram.png)",
        "[ref]\n\n[ref]: https://example.org",
        "A footnote [1]: see https://example.org for the details.",
        "| Link | Note |\n| --- | --- |\n| [x](https://example.org) | `a` |",
    ):
        assert not problems_for(change(value)), value


def test_attribute_lists_may_only_set_an_id():
    for value in (
        '[click]{onclick="alert(1)"}',
        "[click]{.warning}",
        '[click](https://example.org){onclick="alert(1)"}',
        '![logo](logo.png){onerror="alert(1)"}',
        '`code`{onclick="alert(1)"}',
        "`<b>x</b>`{=html}",
        '### Heading {onclick="alert(1)"}',
        '### Heading {#anchor onclick="alert(1)"}',
        '### Heading {#anchor\nonclick="alert(1)"}',
        '### Heading {style="position:fixed"}',
        '::: {onclick="alert(1)"}\ntext\n:::',
        "::: warning\ntext\n:::",
        '> [click]{onclick="alert(1)"}',
        '- [click]{onclick="alert(1)"}',
        "| a |\n| --- |\n| [click]{.cls} |",
        '[click]{k="}" onclick="alert(1)"}',
        "[click]{ #anchor }",
    ):
        problems = problems_for(change(value))
        assert any("attribute" in p or "fenced div" in p for p in problems), (
            value,
            problems,
        )
    assert not problems_for(change("### Another change {#star-2}\n\nText."))
    assert not problems_for(change("A [span]{#star-3} with an anchor."))


def test_braces_and_fences_are_refused_in_code_too():
    # Whether Pandoc reads a line as code depends on quotes, lists, tables and
    # fences, so nothing is exempt and a JSON example cannot use braces.
    for value in (
        '```json\n{"a": 1}\n```',
        '~~~\n{"a": 1}\n~~~',
        'Use `{"a": 1}` as the value.',
        "A model reference is {{ ref('person') }} in dbt.",
        '> ```\n> {"a": 1}\n> ```',
    ):
        problems = problems_for(change(value))
        assert any("attribute" in p for p in problems), (value, problems)
    # An opening fence's info string is attributes to Pandoc.
    for fence in (
        '```{.sql onclick="x"}',
        '```sql {onclick="x"}',
        '~~~ {#a onclick="x"}',
        "```sql {#a}\n```\n```sql {#b .cls}",
    ):
        problems = problems_for(change(fence + "\nselect 1\n" + fence[:3]))
        assert any("attribute" in p for p in problems), (fence, problems)
    assert any("fenced div" in p for p in problems_for(change("a:::b")))
    # Pandoc does not read an unclosed fence as code, so nothing in it is exempt.
    problems = problems_for(change("```\n[click](javascript:alert(1))"))
    assert any("link destination" in p for p in problems), problems
    assert any("close the fenced" in p for p in problems), problems


def test_a_summary_or_title_is_checked_like_the_body():
    # The summary is one line on the page, so a fence around it is only text.
    for summary in (
        "[click](javascript:alert(1))",
        "~~~\\n[click](javascript:alert(1))\\n~~~",
        "[click][ref]\\n\\n[ref]: javascript:alert(1)",
    ):
        problems = problems_for(entry(summary=summary))
        assert any("link destination" in p for p in problems), (summary, problems)
    for summary in ("~~~\\n[click]{.cls}\\n~~~", "A [click]{.cls} summary."):
        problems = problems_for(entry(summary=summary))
        assert any("attribute" in p for p in problems), (summary, problems)
    assert problems_for(entry(extra='title: "[click](javascript:alert(1))"\n'))
    assert problems_for(entry(extra='title: "Release {onclick=1}"\n'))
    assert problems_for(entry(extra='title: "Release <b"\n'))
    assert problems_for(entry(summary="Text\\x01 more."))


def test_a_yaml_metadata_block_in_the_body_is_refused():
    # Pandoc reads one anywhere in the page as document metadata, so
    # `header-includes` can put a script in the page head, and a YAML escape
    # (\x3C) hides the `<` that the raw HTML check looks for.
    for value in (
        '---\nheader-includes: "\\x3Cscript\\x3Ealert(1)\\x3C/script\\x3E"\n---',
        "---\ninclude-after: x\n...",
        "---   \ntitle: pwned\n---",
        "> ---\n> title: pwned\n> ---",
        "- ---\n  title: pwned\n  ---",
        "```yaml\n---\ntitle: example\n---\n```",
    ):
        problems = problems_for(change("Text.\n\n" + value + "\n\nMore."))
        assert any("metadata" in p for p in problems), (value, problems)
    # The entry's own front matter ends at its first closing line.
    problems = problems_for(
        "---\nsummary: Real.\n---\n---\ntitle: pwned\n---\n" + CHANGE
    )
    assert any("metadata" in p for p in problems), problems


def test_a_yaml_metadata_block_is_refused_behind_any_quote_or_list_marker():
    # Pandoc starts a block after any run of quote and list-item markers.
    for value in (
        "+ ---\n  title: pwned\n  ---",
        "* ---\n  title: pwned\n  ---",
        "1. ---\n   title: pwned\n   ---",
        "1) ---\n   title: pwned\n   ---",
        "(1) ---\n    title: pwned\n    ---",
        "a. ---\n   title: pwned\n   ---",
        "#. ---\n   title: pwned\n   ---",
        "(@) ---\n    title: pwned\n    ---",
        "Term\n: ---\n  title: pwned\n  ---",
        "Term\n~ ---\n  title: pwned\n  ---",
        ">---\n>title: pwned\n>---",
        "> > ---\n> > title: pwned\n> > ---",
        "- > ---\n  > title: pwned\n  > ---",
        "> - ---\n>   title: pwned\n>   ---",
        "text[^1]\n\n[^1]:\n    ---\n    title: pwned\n    ---",
        "> ---\ntitle: pwned\n---",
        # Only spaces and tabs are blank to Pandoc, so this line is text.
        "> > ---\n\u2000toc-depth: 9\n---",
    ):
        problems = problems_for(change("Text.\n\n" + value + "\n\nMore."))
        assert any("metadata" in p for p in problems), (value, problems)
    # A horizontal rule with a blank line after it is not metadata.
    for value in ("---", "> ---\n>", "- ---\n", "1. ---\n\n   More.", "***"):
        assert not problems_for(change("Text.\n\n" + value + "\n\nMore.")), value


def test_a_grid_table_is_refused():
    # Its cells are read as blocks, so a `---` line in one starts a metadata
    # block, and Pandoc reads the table inside a quote or a list as well.
    table = "+----------+\n| ---      |\n| lang: x  |\n| ---      |\n+----------+"
    for value in (
        table,
        "> " + table.replace("\n", "\n> "),
        "- " + table.replace("\n", "\n  "),
        "(@) " + table.replace("\n", "\n    "),
        "+---+---+\n| a | b |\n+===+===+\n| 1 | 2 |\n+---+---+",
        "+:--+--:+\n| a | b |\n+---+---+",
    ):
        problems = problems_for(change("Text.\n\n" + value + "\n\nMore."))
        assert any("grid table" in p for p in problems), (value, problems)
    assert not problems_for(change("| a | b |\n| --- | --- |\n| 1 | 2 |"))
    assert not problems_for(change("A plus + sign, a - dash, and 2 + 3 - 1."))


def test_control_characters_are_refused():
    # Pandoc drops a carriage return, so `java\rscript:` is a scheme to it.
    for char in ("\x00", "\x01", "\x0b", "\x0c", "\x1b", "\x7f", "\r"):
        problems = rn.check_entry("2026-10-15.qmd", change(f"Text{char}more."))[1]
        assert any("control character" in p for p in problems), repr(char)
    assert not problems_for(change("A\ttab."))
    assert not rn.check_entry("2026-10-15.qmd", "### Change\r\n\r\nText.\r\n")[1]


def test_raw_html_is_refused_even_without_a_closing_bracket():
    # Pandoc ends a tag at the next `>` anywhere after it, in the next release
    # if need be, so an opening with no `>` of its own is not harmless.
    for value in (
        'Text <div onclick="alert(1)"',
        "<img src=x onerror=alert(1)",
        "a <b",
        "<!-- note",
        "<?php",
        "x </div",
        "<\u00e9clair onclick=1",
    ):
        problems = problems_for(change(value))
        assert any("raw HTML" in p for p in problems), (value, problems)
    problems = load(
        {
            "2026-06-08.qmd": change('Text <div onclick="alert(1)"'),
            "2026-10-15.qmd": entry(body="### Other {#star-2}\n\n> a > quote\n"),
        }
    )[1]
    assert any("raw HTML" in p for p in problems), problems
    assert not problems_for(change("Rows where a < 5 and b <= 3, 4 &lt; 5, 2<3, <3."))


def test_a_fence_closed_by_a_trailing_unicode_space_is_closed():
    # The page keeps the line without that space, which closes the fence there,
    # so what follows is not code and the next fence opens one.
    problems = problems_for(change("~~~text\nExample\n~~~\u3000\n## Smuggled\n~~~"))
    assert any("start each change at `###`" in p for p in problems), problems
    assert any("close the fenced" in p for p in problems), problems


def test_unsafe_markup_is_reported_at_its_source_line():
    text = (
        "Intro.\n\n[click](javascript:alert(1))\n\n[click]{.cls}\n\n---\nkey: value\n"
    )
    problems = rn.publication_problems("entry.qmd", text, first_line=20)
    assert {problem.split(":")[1] for problem in problems} >= {"22", "24", "26"}, (
        problems
    )
    assert not any("javascript" in problem for problem in problems), problems


def test_unclosed_code_fences_cannot_swallow_the_next_release():
    for text in (
        "### Change\n\n```python\nvalue = 1\n",
        "### Change\n\n~~~text\nExample",
    ):
        assert any("close the fenced" in problem for problem in problems_for(text))
    assert any(
        "close the fenced" in problem
        for problem in problems_for(entry(summary="```text\\nExample"))
    )
    assert not problems_for("### Change\n\n```text\nExample\n```")


def test_anchors_are_unique_across_the_page():
    # Both entries carry CHANGE, and with it {#star-11694}.
    problems = load({"2026-06-08.qmd": entry(), "2026-10-15.qmd": entry()})[1]
    assert any("#star-11694" in problem for problem in problems), problems


def test_anchors_in_titles_and_summaries_are_globally_unique():
    for field in ("title", "summary"):
        for ident in ("year-2026", "release-2026-10-15", "star-11694"):
            value = f"[Details]{{#{ident}}}"
            text = (
                entry(summary=value)
                if field == "summary"
                else entry(extra=f'title: "{value}"\n')
            )
            problems = problems_for(text)
            assert any(
                f"#{ident} is already used" in problem for problem in problems
            ), (field, ident, problems)
        text = (
            entry(summary="[Details]{#shared-span}", body="### October\n")
            if field == "summary"
            else entry(extra='title: "[Details]{#shared-span}"\n', body="### October\n")
        )
        problems = load(
            {
                "2026-10-15.qmd": text,
                "2026-06-08.qmd": entry(body="### June\n\n[Details]{#shared-span}\n"),
            }
        )[1]
        assert any("#shared-span is already used" in p for p in problems), problems
    assert problems_for(
        entry(summary="[Details]{#shared}", extra='title: "[Title]{#shared}"\n')
    )
    assert not problems_for(
        entry(summary="[Details]{#summary-id}", extra='title: "[Title]{#title-id}"\n')
    )


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


def test_a_release_missing_its_dataset_record_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = root / "docs/release_notes"
        folder.mkdir(parents=True)
        (folder / "2026-10-15.qmd").write_text(entry(), encoding="utf-8")
        problems = rn.entry_problems(root, [])
    assert any("no available dataset record" in problem for problem in problems), (
        problems
    )


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
