"""What ``strip_to_plaintext`` must never leave in llms.txt.

The data dictionary page carries its field data twice: once as HTML the reader
sees, and once as a ``<script type="application/json">`` island the page's own
script reads. Only the first is prose. If the island survives into
``llms-full.txt``, several hundred kilobytes of JSON land in the middle of the
page text an LLM is given as documentation.

Stripping it is a regex over untrusted-shaped markup, which is exactly the
place a tag filter is usually wrong by a character. These tests pin the two
end-tag spellings that a plain ``</script>`` misses, and check that the failure
mode is dropping too much rather than leaking.

Run with pytest, or directly::

    python tests/test_llms_plaintext.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import generate_llms_txt as gl  # noqa: E402

SECRET = "SHOULD_NOT_APPEAR"

# Every one of these is a script element a browser closes. The regex has to
# close on all of them, or the generic tag-stripper downstream unwraps the
# island and keeps its contents.
CLOSING_FORMS = [
    "</script>",
    "</script >",
    "</script\t>",
    "</script\n>",
    "</SCRIPT>",
    "</Script  >",
]


def _page(closer):
    return f'Before.\n\n<script type="application/json">{{"k": "{SECRET}"}}{closer}\n\nAfter.'


def test_every_closing_form_removes_the_island():
    for closer in CLOSING_FORMS:
        out = gl.strip_to_plaintext(_page(closer))
        assert SECRET not in out, f"leaked through {closer!r}"
        assert "Before." in out and "After." in out, f"over-stripped on {closer!r}"


def test_an_unclosed_script_fails_closed():
    """No end tag at all takes the rest of the text, rather than leaking."""
    out = gl.strip_to_plaintext(f'Before.\n\n<script>{{"k": "{SECRET}"}}\n\nAfter.')
    assert SECRET not in out
    assert "Before." in out
    assert "After." not in out, "the fail-closed branch should have eaten the tail"


def test_a_multiline_island_is_removed_whole():
    out = gl.strip_to_plaintext(
        'A\n<script type="application/json" id="dd-rows">\n'
        f'{{\n  "rows": ["{SECRET}"]\n}}\n'
        "</script>\nB"
    )
    assert SECRET not in out
    assert out.splitlines()[0] == "A"
    assert out.splitlines()[-1] == "B"


def test_prose_that_merely_mentions_a_script_tag_survives():
    """The word is not the tag — an inline code span must not trigger a strip."""
    out = gl.strip_to_plaintext("Load it with a `script` tag. Keep this sentence.")
    assert "Keep this sentence." in out


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
