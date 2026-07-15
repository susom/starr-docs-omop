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
  `docs/omop_data_model.qmd`, `docs/faq.qmd`, `docs/llms.txt`, and `docs/llms-full.txt`.
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

### Edit Other Pages

The remaining pages (`about.qmd`, `getting_access.qmd`, `starr_omop54.qmd`,
`changes_53_to_54.qmd`, `released_datasets.qmd`, `404.qmd`) are hand-authored `.qmd`
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
6. After the PR is merged, publish the site (see [Publishing the Website](README.md#publishing-the-website)).
