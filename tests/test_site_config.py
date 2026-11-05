"""Exercise generator configuration through real subprocess entry points."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from site_config import load_settings  # noqa: E402
import build_site  # noqa: E402


class SiteConfigTests(unittest.TestCase):
    def test_missing_settings_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "_quarto.yml"
            path.write_text("project: {type: website}\n")
            with self.assertRaisesRegex(ValueError, "missing starr"):
                load_settings(path)

    def test_generators_use_configured_paths_and_dataset_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "scripts", root / "scripts")
            docs = root / "docs"
            docs.mkdir()
            shutil.copytree(ROOT / "docs/release_notes", docs / "snapshot_notes")
            shutil.copytree(ROOT / "data/releases", root / "catalog")
            config = yaml.safe_load((ROOT / "docs/_quarto.yml").read_text())
            config["starr"]["releases"].update(
                entries_dir="snapshot_notes",
                catalog_dir="catalog",
                notes_page="notes.qmd",
                datasets_page="datasets.qmd",
                project="synthetic-project",
                dataset_prefix="synthetic_dataset",
            )
            config["project"]["render"] = ["notes.qmd", "datasets.qmd"]
            config["website"]["site-url"] = "https://example.org/docs/"
            (docs / "_quarto.yml").write_text(yaml.safe_dump(config))
            # Launch outside the checkout to catch accidental cwd-based config lookup.
            for script in ("generate_release_notes.py", "generate_llms_txt.py"):
                subprocess.run(
                    [sys.executable, str(root / "scripts" / script)],
                    cwd=tmp,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            datasets = (docs / "datasets.qmd").read_text()
            self.assertIn("synthetic-project.synthetic_dataset_2026_09_10", datasets)
            self.assertIn("notes.qmd#release-2026-09-10", datasets)
            self.assertIn(
                "[Released Datasets](datasets.qmd)", (docs / "notes.qmd").read_text()
            )
            self.assertIn(
                "https://example.org/docs/notes.html", (docs / "llms.txt").read_text()
            )
            self.assertFalse((docs / "release_notes.qmd").exists())

    def test_offline_staging_includes_configured_catalog(self):
        run = subprocess.run
        for catalog_dir in ("data/releases", "catalog", "custom/catalog"):
            with (
                self.subTest(catalog_dir=catalog_dir),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                shutil.copytree(ROOT / "scripts", root / "scripts")
                docs = root / "docs"
                docs.mkdir()
                shutil.copytree(ROOT / "docs/release_notes", docs / "release_notes")
                (root / "data").mkdir()
                shutil.copytree(ROOT / "data/releases", root / catalog_dir)
                config = yaml.safe_load((ROOT / "docs/_quarto.yml").read_text())
                config["starr"]["releases"]["catalog_dir"] = catalog_dir
                (docs / "_quarto.yml").write_text(yaml.safe_dump(config))

                def render(command, *, cwd, check):
                    self.assertEqual(command, ["quarto", "render", "--no-execute"])
                    # Run real hooks in the staged checkout; only Quarto is stubbed.
                    for script in ("generate_release_notes.py", "generate_llms_txt.py"):
                        run(
                            [sys.executable, str(cwd.parent / "scripts" / script)],
                            cwd=cwd,
                            check=check,
                            capture_output=True,
                            text=True,
                        )
                    (cwd / "_site").mkdir()
                    (cwd / "_site/index.html").write_text("Rendered preview")

                with mock.patch.object(
                    build_site.subprocess, "run", side_effect=render
                ):
                    build_site.offline_render(root)
                self.assertIn(
                    "September 2026", (docs / "release_notes.qmd").read_text()
                )
                self.assertIn(
                    "_2026_09_10", (docs / "released_datasets.qmd").read_text()
                )
                self.assertEqual(
                    (docs / "_site/index.html").read_text(), "Rendered preview"
                )

    def test_september_correction_keeps_only_omop_content(self):
        text = (ROOT / "docs/release_notes/2026-09-10.qmd").read_text()
        self.assertNotIn("STARR Common", text)
        self.assertIn("`measurement`", text)
        record = json.loads((ROOT / "data/releases/2026-09-10.json").read_text())
        # This legacy record is first introduced by the unmerged PR.
        self.assertEqual(record["revision"], 1)
        self.assertIsNone(record["correction_reason"])


if __name__ == "__main__":
    unittest.main()
