"""What dbt prose is allowed to become once it reaches a page.

``markdown_prose`` escapes everything that can start a Markdown construct and
then lets exactly one shape back through: ``[label](destination)`` with a
scheme that cannot execute. These tests pin the edge of that one exception.

The edge that matters most is that an **image is not a link**.
``![alt](https://host/x.png)`` differs from a link by a single character, and
that character is not one the escaping pass touches -- so restoring the
brackets around it turns a field description into an ``<img>``: a request to a
third party on page load, out of prose that was only ever promised a link.

Run with pytest, or directly::

    python tests/test_markdown_prose.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import generate_docs as gd  # noqa: E402

# Unescaped `![...](`  -- what Pandoc reads as an image.
LIVE_IMAGE = re.compile(r"!\[[^\]]*\]\(")

# Every spelling of an image these descriptions could plausibly carry, plus the
# ones that try to slip past a naive guard: an empty alt, a doubled `!`, a
# scheme-relative host, an image nested in a link.
IMAGE_SHAPES = [
    "![alt](https://example.com/pixel.png)",
    "![](https://example.com/pixel.png)",
    "!![alt](https://example.com/pixel.png)",
    "![alt](//example.com/pixel.png)",
    "![alt](pixel.png)",
    "text before ![alt](https://example.com/pixel.png) text after",
    "[![badge](https://img.example/b.svg)](https://example.com)",
    "![a](https://example.com/1.png)![b](https://example.com/2.png)",
]


def test_no_image_shape_survives_as_an_image():
    for source in IMAGE_SHAPES:
        assert not LIVE_IMAGE.search(gd.markdown_prose(source)), source


def test_an_image_keeps_its_brackets_escaped():
    assert (
        gd.markdown_prose("![alt](https://example.com/pixel.png)")
        == "!\\[alt\\](https://example.com/pixel.png)"
    )


def test_an_image_inside_a_link_leaves_both_escaped():
    """The outer label holds brackets, which the label charset already refuses."""
    assert gd.markdown_prose(
        "[![badge](https://img.example/b.svg)](https://e.com)"
    ) == ("\\[!\\[badge\\](https://img.example/b.svg)\\](https://e.com)")


def test_an_ordinary_link_is_still_restored():
    source = "See [Athena](https://athena.ohdsi.org) for codes."
    assert gd.markdown_prose(source) == source


def test_an_exclamation_mark_in_prose_does_not_cost_the_next_link():
    """Only a `!` touching the bracket makes an image; one in the sentence does not."""
    source = "Deprecated! [Athena](https://athena.ohdsi.org) has the replacement."
    assert gd.markdown_prose(source) == source


def test_a_relative_destination_is_still_restored():
    source = "[the data model](omop_data_model.qmd)"
    assert gd.markdown_prose(source) == source


def test_an_executable_scheme_is_still_refused():
    assert (
        gd.markdown_prose("[click](javascript:alert)")
        == "\\[click\\](javascript:alert)"
    )


def test_html_is_still_neutralised():
    out = gd.markdown_prose("<img src=x onerror=alert(1)>")
    assert "<" not in out and ">" not in out
    assert "&lt;img" in out


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
