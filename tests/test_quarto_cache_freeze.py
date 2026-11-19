"""Project-level Quarto execution cache and freeze configuration checks.

Verifies the configuration, gitignore/pre-commit boundaries, deterministic FAQ
input generation, offline-staging isolation, and committed freeze snapshot for
``docs/faq.qmd``.

Run with pytest, or directly inside the dev container::

    python tests/test_quarto_cache_freeze.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_site  # noqa: E402


class QuartoCacheFreezeTests(unittest.TestCase):
    def test_project_quarto_execute_enables_cache_and_freeze(self):
        config = yaml.safe_load((ROOT / "docs/_quarto.yml").read_text(encoding="utf-8"))
        execute = config.get("execute", {})
        self.assertIs(execute.get("cache"), True)
        self.assertEqual(execute.get("freeze"), "auto")

    def test_pyproject_declares_jupyter_cache(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        deps = data["project"]["dependencies"]
        self.assertTrue(
            any(re.match(r"^jupyter-cache(?:[<>=!~;\[]|$)", dep) for dep in deps),
            deps,
        )

    def test_gitignore_rules_for_cache_and_freeze(self):
        root_ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        docs_ignore = (
            (ROOT / "docs/.gitignore").read_text(encoding="utf-8").splitlines()
        )
        self.assertIn(".jupyter_cache/", root_ignore)
        self.assertIn("/.jupyter_cache/", docs_ignore)
        self.assertIn("/_freeze/site_libs/", docs_ignore)
        self.assertFalse(
            any(
                line.strip()
                in {
                    "_freeze",
                    "_freeze/",
                    "/_freeze",
                    "/_freeze/",
                    "docs/_freeze",
                    "docs/_freeze/",
                }
                for line in root_ignore + docs_ignore
            )
        )
        if (ROOT / ".git").exists() and shutil.which("git"):
            for kept_path in (
                "docs/_freeze/faq/execute-results/html.json",
                "docs/_freeze/other/execute-results/html.json",
            ):
                kept = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(ROOT),
                        "check-ignore",
                        "--no-index",
                        kept_path,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(kept.returncode, 1, kept.stdout or kept.stderr)
            for ignored_path in (
                "docs/.jupyter_cache/global.db",
                "docs/_freeze/site_libs/clipboard/clipboard.min.js",
            ):
                ignored = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(ROOT),
                        "check-ignore",
                        "--no-index",
                        ignored_path,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(ignored.returncode, 0, ignored_path)

    def test_pre_commit_excludes_freeze_from_whitespace_fixers(self):
        cfg = yaml.safe_load(
            (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        )
        hooks = {
            hook["id"]: hook for repo in cfg["repos"] for hook in repo.get("hooks", [])
        }
        for hook_id in ("trailing-whitespace", "end-of-file-fixer"):
            self.assertEqual(hooks[hook_id].get("exclude"), "^docs/_freeze/")

    def test_committed_faq_freeze_snapshot_is_valid_and_sanitized(self):
        freeze_file = ROOT / "docs/_freeze/faq/execute-results/html.json"
        self.assertTrue(freeze_file.is_file(), f"missing {freeze_file}")
        raw = freeze_file.read_text(encoding="utf-8")
        payload = json.loads(raw)
        self.assertRegex(payload.get("hash", ""), r"^[0-9a-f]{32}$")
        result = payload.get("result", {})
        self.assertEqual(result.get("engine"), "jupyter")
        markdown = result.get("markdown", "")
        self.assertIn("How do I know which event is represented in each row", markdown)
        self.assertIn("vocabulary_id", markdown)
        self.assertIn("condition_concept_id", markdown)
        self.assertIn("SNOMED", markdown)
        for leak in ("/Users/", "/home/", "/root/", "/tmp/"):
            self.assertNotIn(leak, raw)

    def test_generate_faq_is_deterministic_across_repeated_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "scripts", root / "scripts")
            docs = root / "docs"
            docs.mkdir()
            shutil.copy2(ROOT / "docs/_quarto.yml", docs / "_quarto.yml")
            shutil.copytree(ROOT / "docs/faqs", docs / "faqs")
            outputs = []
            for _ in range(2):
                subprocess.run(
                    [sys.executable, str(root / "scripts/generate_faq.py")],
                    cwd=tmp,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                outputs.append((docs / "faq.qmd").read_bytes())
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[0], (ROOT / "docs/faq.qmd").read_bytes())

    def test_offline_staging_ignores_jupyter_cache(self):
        run = subprocess.run
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "scripts", root / "scripts")
            docs = root / "docs"
            docs.mkdir()
            shutil.copy2(ROOT / "docs/_quarto.yml", docs / "_quarto.yml")
            shutil.copytree(ROOT / "docs/release_notes", docs / "release_notes")
            shutil.copytree(ROOT / "docs/faqs", docs / "faqs")
            shutil.copytree(ROOT / "data/releases", root / "data/releases")
            cache_dir = docs / ".jupyter_cache"
            cache_dir.mkdir()
            (cache_dir / "global.db").write_text("local-cache", encoding="utf-8")

            def stub_render(command, *, cwd, check):
                self.assertEqual(command, ["quarto", "render", "--no-execute"])
                self.assertFalse((cwd / ".jupyter_cache").exists())
                for script in (
                    "generate_faq.py",
                    "generate_release_notes.py",
                    "generate_llms_txt.py",
                ):
                    run(
                        [sys.executable, str(cwd.parent / "scripts" / script)],
                        cwd=cwd,
                        check=check,
                        capture_output=True,
                        text=True,
                    )
                (cwd / "_site").mkdir()
                (cwd / "_site/index.html").write_text("ok", encoding="utf-8")

            with mock.patch.object(
                build_site.subprocess, "run", side_effect=stub_render
            ):
                build_site.offline_render(root)
            self.assertTrue((cache_dir / "global.db").is_file())


if __name__ == "__main__":
    unittest.main()
