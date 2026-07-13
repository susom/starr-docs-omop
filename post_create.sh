#!/bin/bash

cd /workspaces/starr-docs-omop
uv init --bare
uv sync
.venv/bin/python -m ipykernel install --user --name=starr-docs
