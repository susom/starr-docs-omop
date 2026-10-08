#!/usr/bin/env python3
"""Optionally synchronize a fixed release, then render the site.

Offline mode is explicit and only for previews: it uses committed model pages
and does not execute FAQ queries. Production never falls back to this mode.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from release_catalog import read_json
from release_pipeline import ROOT, sync_bundle

LOCAL_HOOKS = {"generate_faq.py", "generate_release_notes.py", "generate_llms_txt.py"}
NETWORK_HOOKS = {"generate_docs.py", "generate_exports.py"}
GENERATED_RELEASE_FILES = (
    "release_notes.qmd",
    "released_datasets.qmd",
    "llms.txt",
    "llms-full.txt",
)


def offline_render(root: Path) -> None:
    docs = root / "docs"
    with tempfile.TemporaryDirectory(prefix="starr-docs-preview-") as tmp:
        stage = Path(tmp)
        for name in ("docs", "scripts", "data"):
            shutil.copytree(
                root / name,
                stage / name,
                ignore=shutil.ignore_patterns(
                    "_site", ".quarto", "__pycache__", ".ruff_cache"
                ),
            )
        config_path = stage / "docs/_quarto.yml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        hooks = []
        for command in config["project"]["pre-render"]:
            script = Path(command.split()[0]).name
            if script in NETWORK_HOOKS:
                continue
            if script not in LOCAL_HOOKS:
                raise ValueError(
                    f"offline preview has an unrecognized pre-render hook: {script}"
                )
            hooks.append(command)
        config["project"]["pre-render"] = hooks
        config_path.write_text(
            yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        print(
            "OFFLINE PREVIEW: using committed model pages; FAQ queries are not executed. "
            "The Excel download is present only if previously generated.",
            flush=True,
        )
        subprocess.run(
            ["quarto", "render", "--no-execute"], cwd=stage / "docs", check=True
        )
        # Replace output only after a complete render; a failed build leaves the
        # last preview intact and exits nonzero, never proceeding to deployment.
        with tempfile.TemporaryDirectory(prefix=".release-build-", dir=docs) as local:
            replacement = Path(local) / "_site"
            previous = Path(local) / "previous"
            shutil.copytree(stage / "docs/_site", replacement)
            target = docs / "_site"
            if target.exists():
                target.rename(previous)
            try:
                replacement.rename(target)
            except OSError:
                if previous.exists():
                    previous.rename(target)
                raise
        for name in GENERATED_RELEASE_FILES:
            (docs / name).write_bytes((stage / "docs" / name).read_bytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle",
        type=Path,
        help="an identified, available release bundle to import before rendering",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="credential-free preview only; never used by the deployment job",
    )
    args = parser.parse_args(argv)
    try:
        if args.bundle is not None:
            changed = sync_bundle(ROOT, read_json(args.bundle))
            print(
                "Release synchronized" if changed else "Release already synchronized",
                flush=True,
            )
        if args.offline:
            offline_render(ROOT)
        else:
            subprocess.run(["quarto", "render"], cwd=ROOT / "docs", check=True)
    except (
        OSError,
        ValueError,
        yaml.YAMLError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"Site build stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
