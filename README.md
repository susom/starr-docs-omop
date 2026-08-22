# STARR-OMOP Documentation

Internal documentation for the STARR-OMOP v5.4 Data Model.

## Overview

This repository contains the source code for the STARR-OMOP documentation website, built with [Quarto](https://quarto.org/) as a [Quarto website](https://quarto.org/docs/websites/). It provides:

- **STARR-OMOP v5.4 Data Model** — Auto-generated documentation of OMOP CDM tables from the [starr-data-lake](https://github.com/susom/starr-data-lake) repository
- **Frequently Asked Questions (FAQs)** — Practical guides and answers for working with STARR-OMOP data
- **`llms.txt` / `llms-full.txt`** — Auto-generated machine-readable summaries of the site for LLM tooling

### Related Documentation

- [STARR-Common Documentation](https://github.com/susom/starr-docs-common) — Stanford's native Clarity-derived EHR schema documentation

## Repository Structure

```text
Dockerfile              # Dev container image (R + uv + Quarto)
post_create.sh          # Dev container post-create setup (uv sync + Jupyter kernel)
pyproject.toml          # Python dependencies (managed with uv)
install.R               # R packages installed into the image
scripts/
  generate_docs.py      # Builds omop_data_model.qmd from starr-data-lake dbt YMLs
  generate_exports.py   # Builds the data dictionary page and Excel workbook
  generate_faq.py       # Builds faq.qmd from docs/faqs/q*.qmd entries
  generate_llms_txt.py  # Builds llms.txt and llms-full.txt from the rendered site
docs/
  _quarto.yml           # Quarto site config (pages, navbar, pre-render hooks)
  *.qmd                 # Site pages (about, getting_access, starr_omop54, ...)
  assets/               # Hand-written site scripts (data dictionary grid)
  downloads/            # Generated downloadable artifacts (not committed)
  faqs/                 # Individual FAQ entries (see docs/faqs/README.md)
  styles.css, fonts/    # Stanford theme assets
data/
  dictionary_baseline.json  # Last released field signatures (see below)
tests/                  # Escaping and workbook-layout invariants
```

## How the Site Is Built

The pages `omop_data_model.qmd`, `omop_data_dictionary.qmd`, `faq.qmd`, `llms.txt`, `llms-full.txt`, and the Excel workbook `downloads/starr_omop_data_dictionary.xlsx` are **generated** — do not edit them by hand.

The generated pages are committed so that a diff shows what a dbt change did to the docs. The workbook is not: it is a binary, nothing can be read out of its diff, and the same hooks rebuild it on every render and publish. Expect `docs/downloads/` to be empty in a fresh clone until you render.

They are produced automatically by the `pre-render` hooks declared in [docs/_quarto.yml](docs/_quarto.yml), which run every time you `quarto preview`, `quarto render`, or `quarto publish`, in this order:

1. `scripts/generate_docs.py omop` — sparse-clones [starr-data-lake](https://github.com/susom/starr-data-lake) and extracts table/column metadata from the dbt YML models into `docs/omop_data_model.qmd`. It also rewrites the per-table list under **Data Model Tables** in `docs/_quarto.yml` (see below).
2. `scripts/generate_exports.py omop` — sparse-clones the same repo and flattens the same models into `docs/omop_data_dictionary.qmd` and `docs/downloads/starr_omop_data_dictionary.xlsx`.
3. `scripts/generate_faq.py` — collects every `docs/faqs/q*.qmd` entry into `docs/faq.qmd` (see [docs/faqs/README.md](docs/faqs/README.md)).
4. `scripts/generate_llms_txt.py` — builds `docs/llms.txt` and `docs/llms-full.txt` from the site structure.

The order matters twice: step 4 reads the pages written by steps 1–3, and step 2 must run before it or `llms-full.txt` describes the previous dictionary.

You can also run any of these scripts manually while iterating (see below).

### The sidebar's table list is generated too

`docs/_quarto.yml` is hand-maintained, with one exception: the run of `- text:`/`href: omop_data_model.qmd#…` pairs under **Data Model Tables**. Step 1 rewrites that block from the models it just parsed.

It has to, because that list used to be typed by hand and went stale the moment dbt gained a table — by the time this was noticed the page documented 43 tables and the sidebar offered 30. Quarto re-reads its config after the pre-render hooks, so a repair takes effect in the same render, and the hook is a no-op when the list already matches.

Edit any other part of `_quarto.yml` freely; the rewrite is textual and touches only those lines.

### Why rebuilding a binary every render is safe

Step 2 rewrites a `.xlsx` on every preview, which would normally leave a binary diff in every `git status`. It does not, because both artifacts are byte-reproducible: every timestamp in them — the workbook's `created` property, the provenance block on the page — is derived from the dbt source commit, never from the wall clock. Re-rendering against unchanged models writes identical bytes, so `git status` stays clean and a diff appears only when the dbt models actually move.

The page is committed, so a fresh clone reads correctly before anything is built; the workbook is rebuilt on demand. When a render does produce a diff, commit it: that is the dbt change reaching the site. The page also carries a provenance block naming the dbt commit it was generated from, so a stale checkout is visible on the site itself.

Steps 1 and 2 each clone the dbt repo (~3 s, ~3 MB, shallow and sparse), so the dictionary costs one extra clone per render.

### The data dictionary is tabs of grids

Every table on that page is written into the `.qmd` as an ordinary HTML `<table>`, and [docs/assets/data-dictionary.js](docs/assets/data-dictionary.js) upgrades each one into a [Tabulator](https://tabulator.info) grid after the page loads — search, per-column filters, sorting, resizing, and CSV export of whatever is currently filtered.

The same script also turns the page's 45 sections into tabs: a filterable rail on the left, one panel visible at a time. Each grid is built the first time its tab is opened, because a grid built inside a hidden panel has no layout to measure and would size its columns to zero.

The upgrade is additive, so the static tables are what Quarto's search index, `llms-full.txt`, printing, and a no-JavaScript visitor see — with JavaScript off the page is the plain long scroll it was written as, and `@media print` un-hides every panel so ⌘P still yields the whole dictionary. Tabulator itself is loaded from jsDelivr, pinned to an exact version with an SRI hash; both the version and the hashes live in `scripts/generate_exports.py`, which writes them into the page's front matter. That page also widens itself past the site's usual body column (`grid: body-width:` in the same front matter) so all six columns fit.

Each grid's toolbar carries a **Columns** button — a checklist of every column with reorder arrows and three named presets (Default, Compact, Everything) — plus **Group by**, a density toggle, and expand-all for the clamped descriptions. Choices persist per layout in `localStorage` and are mirrored into the URL fragment, so a filtered, narrowed view can be pasted to a colleague. Phones and laptops keep separate stores: under 768px a grid starts on the Compact set, because four columns is what fits. Clicking any row opens a detail drawer listing every column including the hidden ones, which is what makes hiding a column cheap.

### Adding a column to the dictionary

One entry in `COLUMN_REGISTRY` at the top of `scripts/generate_exports.py`, plus the key that fills it in `build_rows`. Everything else follows from the registry: the `<th>` carries its entry as `data-dd-*` attributes, `data-dictionary.js` configures the grid column by reading them, `styles.css` styles cells by the classes the registry implies, and the workbook takes its width, its wrapping and its About-sheet glossary line from the same place. Nothing in the JavaScript or the stylesheet needs to know the column exists.

`tests/test_workbook_layout.py` pins the half of that contract which used to be positional — a column added after Description once stole its text wrapping, silently.

### Flagging what changed since the last release

`data/dictionary_baseline.json` records one signature per field (`TYPE|Required`) as of the last release. Each run compares the current models against it and writes the difference into the page: a one-line summary in the intro callout, `new` / `changed` badges in the grid, and a "changed only" filter. When the baseline and the models agree the page says so and no badges appear.

The baseline is a committed file and is never updated by an ordinary run — otherwise every render would quietly redefine "since the last release" as "since the last render" and nothing would ever be flagged. Refresh it deliberately, as part of cutting a release:

```bash
python scripts/generate_exports.py omop --update-baseline
```

Commit the result together with the page it produces.

### Tests

`tests/` holds the invariants that are cheap to break and expensive to notice: HTML escaping on the generated page, and the workbook's registry-driven column formatting. They need no fixtures and no network.

```bash
python tests/test_html_escaping.py
python tests/test_workbook_layout.py
# or, if you have pytest: pytest tests
```

## Developer Guide

### Prerequisites

#### Dev container (recommended)

1. **Docker Desktop** — [Download and install Docker](https://www.docker.com/products/docker-desktop/)
2. **Visual Studio Code** — [Download VS Code](https://code.visualstudio.com/)
3. **Dev Containers extension** — Install from the [VS Code marketplace](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
4. **Google Cloud authentication** — Authenticate with the `gcloud` CLI on your host. Your `~/.config/gcloud` directory is mounted into the container (see [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json)), so FAQ code cells that query BigQuery can run during rendering.

When you open the folder in the dev container, [post_create.sh](post_create.sh) runs automatically and:

- Initializes and syncs the Python environment with `uv` (`.venv/`)
- Registers a Jupyter kernel named `starr-docs` used to execute FAQ code cells

#### Local development (without Docker)

1. **Quarto CLI 1.7.29+** — [Download from quarto.org](https://quarto.org/docs/get-started/) or `brew install --cask quarto`
2. **Python 3.12+**
3. **[uv](https://docs.astral.sh/uv/)** for dependency management
4. **Google Cloud authentication** — `gcloud auth application-default login`

```bash
git clone https://github.com/susom/starr-docs-omop.git
cd starr-docs-omop
uv sync
uv run python -m ipykernel install --user --name=starr-docs
```

### Previewing the Site Locally

```bash
uv sync  # run this first if the .venv hasn't been built yet
source .venv/bin/activate
cd docs
quarto preview
```

This runs the pre-render hooks (regenerating the OMOP, FAQ, and llms.txt content) and serves the site with live reload. Because FAQ code cells execute BigQuery queries at render time, you must be authenticated with `gcloud`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to author content (updating the OMOP data model, adding FAQ entries, editing pages) and our conventions for branches, [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/#summary), and pull requests.

## Publishing the Website

The site is published manually to GitHub Pages from a contributor's machine. There is no CI pipeline. After changes are merged into `main`:

```bash
git checkout main
git pull
source .venv/bin/activate
cd docs
quarto publish gh-pages
```

`quarto publish gh-pages` runs the pre-render hooks (regenerating all generated content), builds the site, and pushes the output to the `gh-pages` branch, which GitHub Pages serves.

## Questions?

For questions or issues, please [open an issue](https://github.com/susom/starr-docs-omop/issues/new?title=Documentation%20Issue) in the repository or contact the STARR team.
