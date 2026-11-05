# Contributing to STARR-OMOP Documentation

Thanks for helping improve the STARR-OMOP documentation site. This guide covers how
to author content and the conventions we use for **branches**, **commits**, and
**pull requests**.

For environment setup, previewing, and publishing, see the [README](README.md). This site
is a [Quarto website](https://quarto.org/docs/websites/) — refer to the official Quarto
websites documentation for page layout, navigation, and formatting options.

## Before You Start

- Make sure you can preview the site locally — see [Developer Guide](README.md#developer-guide) in the README.
- Some pages are **generated** and must **not** be edited by hand:
  `docs/omop_data_model.qmd`, `docs/faq.qmd`, `docs/release_notes.qmd`,
  `docs/released_datasets.qmd`, `docs/llms.txt`, and `docs/llms-full.txt`.
  They are produced by the `pre-render` hooks defined in [docs/_quarto.yml](docs/_quarto.yml).

## Authoring Content

### Update the OMOP Data Model

The data model page is regenerated from the [starr-data-lake](https://github.com/susom/starr-data-lake)
dbt models. To refresh it manually:

```bash
uv sync  # run this first if the .venv hasn't been built yet
source .venv/bin/activate
python scripts/generate_docs.py omop
```

This clones the `starr-data-lake` repository (sparse checkout), extracts table and
column metadata from the dbt YML files, and writes `docs/omop_data_model.qmd`. It
also runs automatically as a pre-render hook.

### Add or Edit an FAQ

FAQ entries live in [docs/faqs/](docs/faqs/) and are compiled into the FAQ page
automatically. See [docs/faqs/README.md](docs/faqs/README.md) for the full authoring guide.

### Publish Release Notes

User-facing text is authored and reviewed in [starr-data-lake's PR guide](https://github.com/susom/starr-data-lake/blob/main/docs/getting_started/dbt_pr_template.md).
Keep upstream authoring instructions there. This repository stores the public
snapshot text and renders it; it does not fetch mutable PR descriptions at build time.

#### Validate, import, and render

Run these commands from the repository root in the existing dev container, after
`post_create.sh` has installed the Python environment:

```bash
source .venv/bin/activate
python scripts/generate_release_notes.py --check
# Optional: import an identified, available public bundle supplied by the release owner.
python scripts/release_pipeline.py sync --bundle /path/to/YYYY-MM-DD.json
cd docs
quarto preview --host 0.0.0.0 --port 8080 --no-browser
```

Forward port 8080 in the dev container. For a credential-free static preview,
run `python scripts/build_site.py --offline` from the repository root. It uses
committed model pages and skips FAQ execution. It does not refresh the data
dictionary or prove that a credentialed production render will succeed.

A snapshot has one `docs/release_notes/YYYY-MM-DD.qmd` entry and a matching
`data/releases/YYYY-MM-DD.json` record. The record declares availability,
variants, provenance, and the SHA-256 of the exact entry text. Both Release Notes
and Released Datasets are generated from these same inputs. Identical bundle
imports are no-ops. Invalid content or inconsistent records stop rendering.
The importer cannot prove BigQuery availability; the release owner must verify it.

Do not hand-edit generated pages or imported entries. Historical corrections
require the next record revision, a public correction reason, and an updated entry
checksum. Preserve the deployed source revision when only the text changes.
For the initial legacy September entry in this unmerged PR, apply the reviewed
editorial edit to the source and checksum together. Keep revision 1 because the
record has not reached `main`; do not invent upstream provenance.
January and June have no recorded notes and unknown provenance remains null.

#### Publication checks

Each entry needs an unfenced `###` change heading; subheadings start at `####`.
Anchors must be unique across titles, summaries, and bodies. The validator rejects
invalid filenames/dates, missing records or entries, checksum mismatches, template
comments, raw HTML, executable Quarto content, and private source-system links.
Markdown links must use `http`, `https`, `mailto`, relative paths, or anchors;
only `{#id}` attributes are accepted. Use pipe tables and synthetic examples.
Identifier detection covers wrapped/formatted text and tables, but remains a
backstop: authors and reviewers must verify that public prose contains no PHI.

Release sections appear newest first under year and month headings. Verify the
search, deep links, expand/collapse controls, and Released Datasets cross-links
after regenerating the page and LLM indexes.

#### Upstream follow-up: align release-note authoring and schema scope

The current upstream PR guide and template describe user-impact Markdown in PR
bodies and changelog Docs tags. They do not describe the versioned
`starr-user-note` and availability-record contract supported by this branch's
release tooling. That integration is pending; do not treat it as activated.

Proposed follow-up story in **starr-data-lake**:

- Decide and document the upstream source-of-truth contract before enabling delivery.
- If adopting versioned notes, update the PR guide and changelog template together
  with examples, explicit no-impact decisions, availability records, and corrections.
- Require separate PRs by schema for OMOP and Common user-facing changes so their
  public notes can be reviewed and delivered independently.
- Verify the documented manual export/import against a synthetic release.
- Keep CI, GitHub App setup, automatic delivery, and deployment in separate future
  work requiring the appropriate repository privileges.

This is a proposed story, not an implemented upstream workflow.

### Edit Other Pages

The remaining pages (`about.qmd`, `getting_access.qmd`, `starr_omop54.qmd`,
`changes_53_to_54.qmd`, `404.qmd`) are hand-authored `.qmd`
files in `docs/`. Edit them directly and preview to verify. See the Quarto docs for
[website pages](https://quarto.org/docs/websites/#pages), [navigation](https://quarto.org/docs/websites/website-navigation.html),
and [Markdown authoring](https://quarto.org/docs/authoring/markdown-basics.html).

## Branching

- Create a branch off `main` for every change.
- Use a short, descriptive name prefixed with the kind of change, matching the commit
  types below:

  ```text
  docs/getting-access-updates
  fix/broken-omop-anchor
  feat/add-cohort-faq
  chore/bump-quarto
  ```

- Keep branches focused on a single topic so they are easy to review.

## Commit Messages

We follow the [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/#summary)
specification. Each commit message is structured as:

```text
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

- The **description** is a short, imperative summary (e.g. `fix: correct access-request link`).
- An optional **body** (one blank line after the description) explains the *what* and *why*.
- Optional **footers** follow the [git trailer format](https://git-scm.com/docs/git-interpret-trailers),
  e.g. `Refs: #12` or `Reviewed-by: Jane Doe`.

### Types

| Type       | Use for                                                        |
| ---------- | ------------------------------------------------------------- |
| `docs`     | Documentation content changes (the most common type here)     |
| `feat`     | A new page, FAQ, or capability                                 |
| `fix`      | Fixing a broken link, typo, anchor, or render error           |
| `style`    | Formatting, theme, or CSS changes with no content impact       |
| `refactor` | Restructuring content or scripts without changing behavior    |
| `perf`     | Changes that improve build or render performance              |
| `test`     | Adding or updating checks (if introduced)                     |
| `build`    | Dev container, dependencies, or build tooling                 |
| `ci`       | Continuous integration configuration (if introduced)          |
| `chore`    | Routine maintenance not touching content (e.g. version bumps) |
| `revert`   | Reverting a previous commit                                   |

### Scopes

A scope is an optional noun in parentheses describing the affected area, for example:

```text
docs(getting-access): clarify DUA approval steps
feat(faq): add concept-id lookup entry
fix(data-model): correct generator column parsing
build(deps): bump quarto to 1.7.29
```

Common scopes in this repo: `docs`, `faq`, `scripts`, `data-model`, `deps`, `ci`.

### Breaking Changes

Breaking changes are indicated with a `!` before the colon and/or a `BREAKING CHANGE:`
footer (this correlates with a MAJOR bump in [Semantic Versioning](https://semver.org/)):

```text
feat!: restructure site navigation

BREAKING CHANGE: existing deep links to v5.3 pages have moved.
```

### Examples

```text
docs: correct spelling of CHANGELOG
fix(faq): repair broken BigQuery link in q1
feat(faq): add polish on concept lookup example
refactor(scripts): simplify llms.txt generation
```

## Pull Requests

1. Create a branch off `main` (see [Branching](#branching)).
2. Make your changes — edit `.qmd` pages or add FAQ entries.
3. Preview locally with `quarto preview` and confirm everything renders.
4. Confirm you did **not** hand-edit any generated files.
5. Open a pull request; the [pull request template](.github/pull_request_template.md)
   is applied automatically — fill it in.
6. After merge, an authorized maintainer publishes manually using
   [Publishing the Website](README.md#publishing-the-website).
