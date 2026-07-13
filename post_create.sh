#!/bin/bash
set -e

# Run from the repository root (the directory containing this script) so the
# hook is not tied to a specific workspace folder name.
cd "$(dirname "$0")"

# pyproject.toml is committed, so only sync dependencies — no `uv init`.
uv sync
.venv/bin/python -m ipykernel install --user --name=starr-docs
