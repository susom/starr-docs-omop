"""Release contract, availability, history, and delivery invariants.

Run inside Docker with ``python tests/test_release_pipeline.py``.
Source repositories are synthetic, local Git histories; no service is contacted.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_site as build  # noqa: E402
import generate_docs as models  # noqa: E402
import generate_release_notes as rn  # noqa: E402
import release_catalog as catalog  # noqa: E402
import release_pipeline as pipeline  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-10-15"
PATH = "CHANGELOG-example-star-12345.adoc"
NOTE = {
    "schema_version": 1,
    "id": "star-12345",
    "audiences": ["STARR OMOP 5.4"],
    "title": "Observation periods include notes",
    "impact": "A note-only encounter now contributes to `observation_period`.",
    "action": "None. Existing columns are unchanged.",
    "details": "| Table | Change |\n| --- | --- |\n| `observation_period` | Note-only encounters are included. |",
}


def fragment(note=NOTE):
    return (
        "- An internal changelog title, not used as the public title.\n\n////\n"
        + pipeline.yaml.safe_dump({"starr-user-note": note}, sort_keys=False)
        + "////\n"
    )


class ReleasePipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Release fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "baseline.txt").write_text("baseline\n")
        self.previous = self.commit()
        (self.repo / PATH).write_text(fragment(), encoding="utf-8")
        self.source = self.commit()
        self.record = pipeline.prepare_record(
            self.repo, self.previous, self.source, DATE, list(catalog.VARIANTS)
        )
        self.record.update(
            status="available",
            available_at=f"{DATE}T18:00:00Z",
            availability_evidence="private:deployment-run-verified-by-release-owner",
        )
        self.docs = self.root / "docs-repo"
        records = self.docs / catalog.CATALOG_DIR
        records.mkdir(parents=True)
        for date in catalog.LEGACY_DATES:
            shutil.copy2(ROOT / catalog.CATALOG_DIR / f"{date}.json", records)
        entries = self.docs / "docs/release_notes"
        entries.mkdir(parents=True)
        shutil.copy2(ROOT / "docs/release_notes/2026-09-10.qmd", entries)

    def git(self, *args):
        return pipeline.git(self.repo, *args).strip()

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "Fixture change")
        return self.git("rev-parse", "HEAD")

    def bundle(self):
        return pipeline.export_bundle(self.repo, self.record)

    def snapshot(self):
        return {
            path.relative_to(self.docs): path.read_bytes()
            for path in self.docs.rglob("*")
            if path.is_file()
        }

    def test_exports_exact_reviewed_text_and_allowlisted_provenance(self):
        bundle = self.bundle()
        self.assertEqual(
            bundle["entry"],
            pipeline.render_note(pipeline.parse_note(fragment(), PATH), DATE),
        )
        self.assertEqual(bundle["release"]["source_revision"], self.source)
        self.assertEqual(bundle["release"]["note_ids"], ["star-12345"])
        serialized = catalog.json_text(bundle)
        for private in (
            "availability_evidence",
            "deployment-run",
            "previous_revision",
            PATH,
            "internal changelog",
        ):
            self.assertNotIn(private, serialized)
        self.assertEqual(bundle, self.bundle())
        pipeline.parse_bundle(bundle)

    def test_working_tree_and_later_commits_cannot_change_the_frozen_release(self):
        before = self.bundle()
        (self.repo / PATH).write_text("edited but not committed\n")
        self.assertEqual(before, self.bundle())
        self.commit()
        self.assertEqual(before, self.bundle())

    def test_folded_fragment_is_recovered_from_git_history(self):
        expected = self.bundle()["entry"]
        (self.repo / PATH).unlink()
        self.record["source_revision"] = self.commit()
        self.assertEqual(expected, self.bundle()["entry"])

    def test_release_range_excludes_notes_that_shipped_previously(self):
        self.record["previous_revision"] = self.source
        self.record["notes"] = []
        self.record["notes_status"] = "no-user-facing-changes"
        self.record["no_changes_reason"] = (
            "This snapshot refreshes source data without changing the interface."
        )
        bundle = self.bundle()
        self.assertEqual(bundle["release"]["note_ids"], [])
        self.assertIn("No user-facing changes", bundle["entry"])

    def test_missing_fragment_decisions_stop_export(self):
        self.record["notes"] = []
        with self.assertRaisesRegex(ValueError, "unaccounted"):
            self.bundle()

    def test_foreign_and_duplicate_decisions_stop_export(self):
        for value in (
            self.record["notes"] * 2,
            [
                {
                    "path": "CHANGELOG-outside.adoc",
                    "disposition": "include",
                    "reason": None,
                }
            ],
        ):
            with self.subTest(value=value):
                record = copy.deepcopy(self.record)
                record["notes"] = value
                with self.assertRaisesRegex(ValueError, "duplicate or out-of-release"):
                    pipeline.export_bundle(self.repo, record)

    def test_legacy_or_held_fragments_need_explicit_accounting(self):
        path = "CHANGELOG-legacy-star-10000.adoc"
        (self.repo / path).write_text("- Legacy fragment without a user-note block\n")
        self.record["source_revision"] = self.commit()
        self.record["notes"].append(
            {"path": path, "disposition": "unresolved", "reason": None}
        )
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.bundle()
        self.record["notes"][-1].update(
            disposition="omit", reason="Not enabled in the released OMOP variants."
        )
        self.assertEqual(self.bundle()["release"]["note_ids"], ["star-12345"])
        self.record["notes"][-1]["reason"] = ""
        with self.assertRaisesRegex(ValueError, "omission reason"):
            self.bundle()

    def test_drafts_can_be_built_but_cannot_be_imported(self):
        self.record.update(
            status="draft", available_at=None, availability_evidence=None
        )
        bundle = self.bundle()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "available"):
            pipeline.sync_bundle(self.docs, bundle)
        self.assertEqual(before, self.snapshot())

    def test_availability_and_public_schema_fail_closed(self):
        cases = {
            "availability_evidence": None,
            "available_at": f"{DATE}T12:00:00",
            "source_revision": "main",
            "dataset_suffix": "_2026_10_16",
            "variants": ["unknown"],
            "schema_version": True,
            "notes_status": None,
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                record = copy.deepcopy(self.record)
                record[field] = value
                with self.assertRaises(ValueError):
                    pipeline.export_bundle(self.repo, record)

    def test_duplicate_yaml_keys_and_unknown_note_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            pipeline.load_yaml("status: draft\nstatus: available\n")
        note = dict(NOTE, publication_magic=True)
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            pipeline.parse_note(fragment(note), PATH)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            pipeline.parse_note(fragment() + fragment(), PATH)

    def test_title_impact_and_action_are_required_public_fields(self):
        for field in ("title", "impact", "action"):
            note = copy.deepcopy(NOTE)
            note[field] = ""
            with self.subTest(field=field), self.assertRaises(ValueError):
                pipeline.parse_note(fragment(note), PATH)
        for field, text in (
            ("title", "x" * 141),
            ("details", "### A second change"),
            ("impact", "{{< include private.txt >}}"),
            ("action", "MRN:\n**1234567**"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                pipeline.parse_note(fragment(dict(NOTE, **{field: text})), PATH)

    def test_code_pr_requires_changed_note_even_without_a_docs_tag(self):
        (self.repo / "dbt/models").mkdir(parents=True)
        (self.repo / "dbt/models/model.sql").write_text("select 1\n")
        head = self.commit()
        pipeline.check_pr(self.repo, self.previous, head)
        with self.assertRaisesRegex(ValueError, "require a new/updated note"):
            pipeline.check_pr(self.repo, self.source, head)

    def test_no_impact_reason_is_a_valid_code_review_decision(self):
        note = {key: NOTE[key] for key in ("schema_version", "id", "audiences")}
        note["no_user_impact"] = (
            "Refactoring only; the output rows and columns are unchanged."
        )
        (self.repo / PATH).write_text(fragment(note))
        (self.repo / "src").mkdir()
        (self.repo / "src/change.py").write_text("value = 1\n")
        head = self.commit()
        pipeline.check_pr(self.repo, self.source, head)
        prepared = pipeline.prepare_record(self.repo, self.source, head, DATE, ["core"])
        self.assertEqual(prepared["notes"][0]["disposition"], "omit")
        self.assertEqual(prepared["notes_status"], "no-user-facing-changes")
        self.assertIsNone(prepared["no_changes_reason"])

    def test_no_changes_is_explicit_and_cannot_hide_included_notes(self):
        self.record["notes_status"] = "no-user-facing-changes"
        self.record["no_changes_reason"] = "A refresh only."
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.bundle()
        self.record["notes"][0].update(
            disposition="omit", reason="The feature is not enabled for this release."
        )
        self.record["no_changes_reason"] = None
        with self.assertRaisesRegex(ValueError, "no_changes_reason"):
            self.bundle()

    def test_sync_is_idempotent_including_file_timestamps(self):
        bundle = self.bundle()
        self.assertTrue(pipeline.sync_bundle(self.docs, bundle))
        before = self.snapshot()
        times = {
            path: path.stat().st_mtime_ns
            for path in self.docs.rglob("*")
            if path.is_file()
        }
        self.assertFalse(pipeline.sync_bundle(self.docs, bundle))
        self.assertEqual(before, self.snapshot())
        self.assertEqual(times, {path: path.stat().st_mtime_ns for path in times})
        self.assertFalse(
            catalog.entry_problems(self.docs, catalog.load_catalog(self.docs))
        )

    def test_checksum_unknown_keys_and_note_id_mismatch_stop_import(self):
        original = self.bundle()
        for kind in ("checksum", "private-key", "note_ids"):
            with self.subTest(kind=kind):
                bundle = copy.deepcopy(original)
                if kind == "checksum":
                    bundle["entry"] += "Unexpected change.\n"
                elif kind == "private-key":
                    bundle["release"]["private_audit_log"] = "not public"
                else:
                    bundle["release"]["note_ids"] = ["different-note"]
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    pipeline.sync_bundle(self.docs, bundle)
                self.assertEqual(before, self.snapshot())

    def test_historical_corrections_need_next_revision_and_reason(self):
        original = self.bundle()
        pipeline.sync_bundle(self.docs, original)
        changed = copy.deepcopy(original)
        changed["entry"] = changed["entry"].replace(
            "Note-only encounters are included.",
            "Note-only encounters are now included.",
        )
        changed["release"]["entry_sha256"] = catalog.text_hash(changed["entry"])
        with self.assertRaisesRegex(ValueError, "revision 2"):
            pipeline.sync_bundle(self.docs, changed)
        changed["release"]["revision"] = 2
        with self.assertRaisesRegex(ValueError, "correction_reason"):
            pipeline.sync_bundle(self.docs, changed)
        changed["release"]["correction_reason"] = "Clarify the encounter description."
        self.assertTrue(pipeline.sync_bundle(self.docs, changed))
        self.assertFalse(pipeline.sync_bundle(self.docs, changed))
        with self.assertRaisesRegex(ValueError, "revision 3"):
            pipeline.sync_bundle(self.docs, original)

    def test_text_corrections_keep_the_actual_deployed_source_revision(self):
        original = self.bundle()
        pipeline.sync_bundle(self.docs, original)
        corrected = dict(
            NOTE,
            impact="A note-only encounter contributes to observation period coverage.",
        )
        (self.repo / PATH).write_text(fragment(corrected))
        text_revision = self.commit()
        self.record["notes"][0]["text_revision"] = text_revision
        with self.assertRaisesRegex(ValueError, "historical correction"):
            self.bundle()
        self.record.update(
            revision=2, correction_reason="Clarify the observation period impact."
        )
        bundle = self.bundle()
        self.assertEqual(bundle["release"]["source_revision"], self.source)
        self.assertIn(corrected["impact"], bundle["entry"])
        self.assertNotIn("text_revision", catalog.json_text(bundle))
        self.assertTrue(pipeline.sync_bundle(self.docs, bundle))
        (self.repo / PATH).write_text(fragment(dict(corrected, id="different-note")))
        self.record["notes"][0]["text_revision"] = self.commit()
        with self.assertRaisesRegex(ValueError, "preserve its original id"):
            self.bundle()

    def test_history_check_rejects_unversioned_edits_and_removed_records(self):
        self.repo = self.docs
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Release fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        base = self.commit()
        pipeline.check_history(self.docs, base)
        path = self.docs / catalog.CATALOG_DIR / "2026-09-10.json"
        original = catalog.read_json(path)
        changed = dict(original, variants=["core"])
        path.write_text(catalog.json_text(changed))
        with self.assertRaisesRegex(ValueError, "revision 2"):
            pipeline.check_history(self.docs, base)
        changed.update(
            revision=2, correction_reason="Correct the recorded variant list."
        )
        path.write_text(catalog.json_text(changed))
        pipeline.check_history(self.docs, base)
        path.unlink()
        (self.docs / "docs/release_notes/2026-09-10.qmd").unlink()
        with self.assertRaisesRegex(ValueError, "cannot be removed"):
            pipeline.check_history(self.docs, base)

    def test_crlf_entry_checksums_are_checked_against_exact_bytes(self):
        bundle = self.bundle()
        bundle["entry"] = bundle["entry"].replace("\n", "\r\n")
        bundle["release"]["entry_sha256"] = catalog.text_hash(bundle["entry"])
        self.assertTrue(pipeline.sync_bundle(self.docs, bundle))
        self.assertFalse(
            catalog.entry_problems(self.docs, catalog.load_catalog(self.docs))
        )
        self.assertFalse(pipeline.sync_bundle(self.docs, bundle))

    def test_missing_reason_and_misnamed_catalog_files_are_not_silent(self):
        for reason in ("", "None", "N/A", "TODO"):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                catalog.require_reason(reason, "omission reason")
        path = self.docs / catalog.CATALOG_DIR / "2026-09-10.json"
        path.rename(path.with_suffix(".yaml"))
        with self.assertRaisesRegex(ValueError, "must be YYYY-MM-DD.json"):
            catalog.load_catalog(self.docs)

    def test_existing_corruption_is_not_silently_overwritten(self):
        path = self.docs / "docs/release_notes/2026-09-10.qmd"
        path.write_text(path.read_text() + "Unversioned edit.\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "checksum"):
            pipeline.sync_bundle(self.docs, self.bundle())
        self.assertEqual(before, self.snapshot())

    def test_a_failed_metadata_write_rolls_back_the_entry(self):
        write = pipeline.atomic_write

        def fail_record(path, content):
            if path.suffix == ".json":
                raise OSError("simulated record write failure")
            write(path, content)

        before = self.snapshot()
        with mock.patch.object(pipeline, "atomic_write", side_effect=fail_record):
            with self.assertRaisesRegex(OSError, "simulated"):
                pipeline.sync_bundle(self.docs, self.bundle())
        self.assertEqual(before, self.snapshot())

    def test_both_pages_use_the_same_record_and_partial_variants(self):
        self.record["variants"] = ["core", "lite"]
        pipeline.sync_bundle(self.docs, self.bundle())
        with mock.patch.object(
            rn, "__file__", str(self.docs / "scripts/generate_release_notes.py")
        ):
            self.assertEqual(rn.main([]), 0)
        notes = (self.docs / "docs/release_notes.qmd").read_text()
        datasets = (self.docs / "docs/released_datasets.qmd").read_text()
        self.assertIn("## October 2026 {#release-2026-10-15}", notes)
        self.assertIn("**October 15, 2026** (`_2026_10_15`)", datasets)
        self.assertNotIn("starr_omop_cdm54_confidential_1pcent_2026_10_15", datasets)
        latest = datasets.split("## Latest Datasets")[1].split("## Release History")[0]
        self.assertEqual(latest.count("`_2026_10_15`"), 2)
        self.assertEqual(latest.count("`_2026_09_10`"), 2)
        self.assertIn("Release notes were not recorded", datasets)
        first = self.snapshot()
        with mock.patch.object(
            rn, "__file__", str(self.docs / "scripts/generate_release_notes.py")
        ):
            self.assertEqual(rn.main([]), 0)
        self.assertEqual(first, self.snapshot())

    def test_generator_failure_does_not_replace_either_page(self):
        for name in ("release_notes.qmd", "released_datasets.qmd"):
            (self.docs / "docs" / name).write_text("previous page\n")
        (self.docs / "docs/release_notes/2026-10-15.qmd").write_text(
            "### Unregistered\n"
        )
        before = self.snapshot()
        with mock.patch.object(
            rn, "__file__", str(self.docs / "scripts/generate_release_notes.py")
        ):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(rn.main([]), 1)
        self.assertEqual(before, self.snapshot())

    def test_export_batch_never_reuses_stale_artifacts(self):
        records = self.root / "records"
        records.mkdir()
        path = records / f"{DATE}.yaml"
        path.write_text(pipeline.yaml.safe_dump(self.record))
        output = self.root / "bundles"
        self.assertEqual(pipeline.export_available(self.repo, records, output), [DATE])
        self.assertEqual(json.loads((output / "dates.json").read_text()), [DATE])
        with self.assertRaisesRegex(ValueError, "must be empty"):
            pipeline.export_available(self.repo, records, output)

    def test_sync_failure_never_falls_back_to_a_stale_build(self):
        with mock.patch.object(build, "offline_render") as render:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    build.main(
                        ["--offline", "--bundle", str(self.root / "missing.json")]
                    ),
                    1,
                )
            render.assert_not_called()

    def test_live_render_failure_never_falls_back_to_offline(self):
        with mock.patch.object(
            build.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "quarto"),
        ):
            with mock.patch.object(build, "offline_render") as offline:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(build.main([]), 1)
                offline.assert_not_called()

    def test_delivery_body_preserves_the_actual_pr_template(self):
        pipeline.sync_bundle(self.docs, self.bundle())
        (self.docs / ".github").mkdir()
        template = ROOT / ".github/pull_request_template.md"
        shutil.copy2(template, self.docs / ".github/pull_request_template.md")
        body = pipeline.delivery_body(self.docs, DATE)
        before = template.read_text(encoding="utf-8")
        self.assertEqual(
            [line for line in before.splitlines() if line.startswith("#")],
            [line for line in body.splitlines() if line.startswith("#")],
        )
        for line in before.splitlines():
            if "<!--" in line:
                self.assertIn(line, body)
            if line.startswith("- ["):
                self.assertIn(
                    line.replace("[ ]", "[x]")
                    if "Release notes entry" in line
                    else line,
                    body,
                )
        self.assertIn("python scripts/build_site.py --offline` - passed", body)
        self.assertIn(f"`data/releases/{DATE}.json`", body)

    def test_supplied_model_checkout_is_used_without_network_or_cleanup(self):
        folder = self.repo / models.MODEL_CONFIGS["omop"]["yml_path"]
        folder.mkdir(parents=True)
        generator = models.DocGenerator(self.docs, models.MODEL_CONFIGS["omop"])
        with mock.patch.dict(
            "os.environ", {"STARR_DATA_LAKE_CHECKOUT": str(self.repo)}
        ):
            with mock.patch.object(models.subprocess, "run") as run:
                self.assertEqual(generator.clone_repository(), self.repo)
                run.assert_not_called()
        generator.cleanup()
        self.assertTrue(folder.exists())

    def test_unavailable_model_checkout_fails_instead_of_cloning(self):
        generator = models.DocGenerator(self.docs, models.MODEL_CONFIGS["omop"])
        with mock.patch.dict(
            "os.environ", {"STARR_DATA_LAKE_CHECKOUT": str(self.root / "missing")}
        ):
            with mock.patch.object(models.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "Git checkout"):
                    generator.clone_repository()
                run.assert_not_called()
        with mock.patch.dict("os.environ", {"STARR_DATA_LAKE_CHECKOUT": ""}):
            with mock.patch.object(models.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "must not be empty"):
                    generator.clone_repository()
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
