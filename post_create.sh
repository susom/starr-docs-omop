#!/bin/bash
set -e

# Run from the repository root (the directory containing this script) so the
# hook is not tied to a specific workspace folder name. ${BASH_SOURCE[0]} is
# used instead of $0 so it resolves correctly regardless of how the script is
# invoked (e.g. bash post_create.sh, sourced, or via an absolute path).
cd "$(dirname "${BASH_SOURCE[0]}")"

# pyproject.toml is committed, so only sync dependencies — no `uv init`.
uv sync
.venv/bin/python -m ipykernel install --user --name=starr-docs
