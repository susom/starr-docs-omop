# Authoring Release Notes

This folder holds one file per STARR-OMOP dataset release. They are compiled into the [Release Notes](../release_notes.qmd) page automatically. `docs/release_notes.qmd` is a **generated file**: do not edit it by hand, because your changes will be overwritten.

## How the Page Is Organized

The page lists releases newest first, grouped by year and month:

- `# 2026`: one heading per year.
- `## September 2026`: one heading per release, with the snapshot date and dataset suffix under it. This heading comes from the release's date, and the `title` front matter key replaces it.
- `###`: the entry's changes, as written.

An index under the introduction links every release by year and month, and the table of contents lists every year and month; it leaves out the changes, whose headings are whole sentences. Quarto's site search returns one result per `##` section, so searching for a table or field name such as `operator_concept_id` lists the months that changed it.

Each year and month heading opens and closes its section. The newest year and the newest release start open, and every other one starts closed, so a reader sees the latest changes first and older months as a short list. **Expand all** and **Collapse all** act on every section at once. A link into a closed section opens it, whether it comes from the index, the table of contents, a search result or a shared URL. In browsers that support `hidden="until-found"`, find-in-page searches closed sections too. Printing shows every section. The behaviour is [docs/assets/release-notes.js](../assets/release-notes.js), and the look is the release notes section of [docs/styles.css](../styles.css); both act only on this page. Without JavaScript, every section is open.

## Where the Text Comes From

Release notes describe what changed **for users**, in the words of the engineers who made each change. They come from starr-data-lake (see STAR-12576), and nobody rewrites them on the way:

- A change is included when its changelog entry carries the `STARR OMOP 5.4 Docs` tag **and** its pull request has a filled **User Impact / User-Facing Changes** section. A change without the tag is left out, however good its text.
- The User Impact section is published as written, under a heading taken from the first sentence of the tagged changelog entry.

To change what a note says, edit the pull request's User Impact section and rebuild the entry. To leave a change out, remove its `STARR OMOP 5.4 Docs` tag.

## Adding a Release

Once starr-data-lake's release-notes extractor writes entries (STAR-12576), copy its file here unchanged. Until then, or to build one by hand:

1. Name the file for the snapshot date in the release's dataset names: `2026-09-10.qmd` for datasets ending in `_2026_09_10`. Take it from the dataset names (the `dataset_suffix` of starr-data-lake's production OMOP deployment), not from `CHANGELOG.adoc`. The changelog section can be dated a few days later: the section for `_2026_09_10` is dated 2026-09-11.
2. Add one `###` section per tagged change, laid out as in [_template.qmd](_template.qmd): the first sentence of its changelog entry as the heading, an anchor built from its Jira key, such as `{#star-11694}`, and then the pull request's User Impact section, unchanged. `#` and `##` belong to the page. Anchors must be unique across the page, so a ticket that returns in a later release gets a suffix there, such as `{#star-11694-2026-11}`.
3. Front matter is optional. A `summary` adds one sentence under the release heading, and `title` replaces the default heading, the month and year (`October 2026`).
4. Add the release's datasets to [Released Datasets](../released_datasets.qmd) if they are not listed yet. The generator warns when a release has notes but no datasets.
5. Preview with `quarto preview` from `docs/`. The page is rebuilt on every render.

## What the Generator Refuses

[scripts/generate_release_notes.py](../../scripts/generate_release_notes.py) runs as a `pre-render` hook. It stops the render, so a bad entry is never published, when an entry:

- is not named `YYYY-MM-DD.qmd` with a real date;
- has an unclosed front matter block, keys other than `summary` and `title`, or a `summary` or `title` that is empty or not text;
- uses a `#` or `##` heading, which belong to the page;
- still contains template guidance (any HTML comment);
- contains an SSN-shaped number or `MRN` followed by digits, even inside a code block;
- reuses an anchor that another section already has, including the page's own `#year-YYYY` and `#release-YYYY-MM-DD` anchors.

Text is published as written, so the identifier check is only a backstop for example values that look real. If it fires, fix the example in the pull request and rebuild the entry.

To check entries without rendering, run this from the repository root:

```bash
python scripts/generate_release_notes.py --check
```
