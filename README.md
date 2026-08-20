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
  generate_exports.py   # Builds the data dictionary page and Excel workbook (manual)
  generate_faq.py       # Builds faq.qmd from docs/faqs/q*.qmd entries
  generate_llms_txt.py  # Builds llms.txt and llms-full.txt from the rendered site
docs/
  _quarto.yml           # Quarto site config (pages, navbar, pre-render hooks)
  *.qmd                 # Site pages (about, getting_access, starr_omop54, ...)
  assets/               # Hand-written site scripts (data dictionary grid)
  downloads/            # Generated downloadable artifacts (Excel data dictionary)
  faqs/                 # Individual FAQ entries (see docs/faqs/README.md)
  styles.css, fonts/    # Stanford theme assets
```

## How the Site Is Built

The pages `omop_data_model.qmd`, `faq.qmd`, `llms.txt`, and `llms-full.txt` are **generated** — do not edit them by hand.

They are produced automatically by the `pre-render` hooks declared in [docs/_quarto.yml](docs/_quarto.yml), which run every time you `quarto preview`, `quarto render`, or `quarto publish`, in this order:

1. `scripts/generate_docs.py omop` — sparse-clones [starr-data-lake](https://github.com/susom/starr-data-lake) and extracts table/column metadata from the dbt YML models into `docs/omop_data_model.qmd`.
2. `scripts/generate_faq.py` — collects every `docs/faqs/q*.qmd` entry into `docs/faq.qmd` (see [docs/faqs/README.md](docs/faqs/README.md)).
3. `scripts/generate_llms_txt.py` — builds `docs/llms.txt` and `docs/llms-full.txt` from the site structure.

You can also run any of these scripts manually while iterating (see below).

### The data dictionary is generated manually

`docs/omop_data_dictionary.qmd` and `docs/downloads/starr_omop_data_dictionary.xlsx` are also generated — and also must not be edited by hand — but they are **not** produced by a `pre-render` hook. The workbook is a binary file, so regenerating it on every preview would leave noise in every `git status`.

Regenerate both when the dbt models change, then commit them:

```bash
python scripts/generate_exports.py omop
```

Both artifacts are committed so the page and its download are available from a fresh clone without a build step. The page carries a provenance block naming the dbt commit it was generated from, so a stale copy is visible on the site itself.

### The data dictionary tables are grids

Every table on that page is written into the `.qmd` as an ordinary HTML `<table>`, and [docs/assets/data-dictionary.js](docs/assets/data-dictionary.js) upgrades each one into a [Tabulator](https://tabulator.info) grid after the page loads — search, per-column filters, sorting, resizing, and CSV export of whatever is currently filtered.

The upgrade is additive, so the static tables are what Quarto's search index, `llms-full.txt`, printing, and a no-JavaScript visitor see. Tabulator itself is loaded from jsDelivr, pinned to an exact version with an SRI hash; both the version and the hashes live in `scripts/generate_exports.py`, which writes them into the page's front matter. That page also widens itself past the site's usual body column (`grid: body-width:` in the same front matter) so all six columns fit.

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
