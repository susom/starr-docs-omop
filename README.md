# STARR-OMOP Documentation

Internal documentation for the STARR-OMOP v5.4 Data Model.

## Overview

This repository contains the source code for the STARR-OMOP documentation website, built with [Quarto](https://quarto.org/). It provides:

- **STARR-OMOP v5.4 Data Model** - Auto-generated documentation of OMOP CDM tables from the [starr-data-lake](https://github.com/susom/starr-data-lake) repository
- **Frequently Asked Questions (FAQs)** - Practical guides and answers for working with STARR-OMOP data

### Related Documentation

- [STARR-Common Documentation](https://github.com/susom/starr-docs-common) - Stanford's native Clarity-derived EHR schema documentation

## Developer Guide

### Prerequisites

#### For devcontainer development (recommended)

1. **Docker Desktop** - [Download and install Docker](https://www.docker.com/products/docker-desktop/)
2. **Visual Studio Code** - [Download VS Code](https://code.visualstudio.com/)
3. **Remote - Containers Extension** - Install from the [VS Code marketplace](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
4. **Google Cloud Authentication** - You must be authenticated with `gcloud` CLI

#### For local development (without Docker)

1. **Quarto CLI** - [Download from quarto.org](https://quarto.org/docs/get-started/) or `brew install --cask quarto`
2. **Python 3.12+** with `pip` (or `uv` for dependency management)
3. **Python packages**: `pip install jupyter nbformat tabulate pyyaml`
4. **Google Cloud Authentication**

### Getting Started

```bash
git clone https://github.com/susom/starr-docs-omop.git
cd starr-docs-omop
```

### Updating OMOP Documentation

```bash
source .venv/bin/activate
python scripts/generate_docs.py omop
```

This command:
1. Clones the starr-data-lake repository (sparse checkout)
2. Extracts table and column metadata from dbt YML files
3. Generates `docs/omop_data_model.qmd` with detailed table documentation

### Creating FAQ Entries

1. Create a new file in `docs/faqs/` following the naming pattern `q_<description>.qmd`
2. Use `docs/faqs/faq_template.qmd` as a starting point
3. Regenerate: `python scripts/generate_faq.py`

### Publishing the Website

```bash
cd docs
quarto publish gh-pages
```

### Questions?

For questions or issues, please open an issue in the repository or contact the STARR team.
