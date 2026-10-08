#!/usr/bin/env python3
"""Validate versioned user notes, freeze a dataset release, and import it.

All source reads use explicit Git revisions. Neither PR bodies nor the network
are consulted. Only the allowlisted public bundle crosses repository boundaries.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

import generate_release_notes as rn
import release_catalog as catalog

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = "STARR OMOP 5.4"
FRAGMENT = re.compile(r"CHANGELOG-[A-Za-z0-9][A-Za-z0-9._-]*\.adoc")
BLOCK = re.compile(
    r"^////[ \t]*\n(starr-user-note:[^\n]*\n.*?)^////[ \t]*(?:\n|\Z)",
    re.MULTILINE | re.DOTALL,
)
USER_FACING_PATHS = ("dbt/", "src/", "deployments/")
RECORD_FIELDS = {
    "schema_version",
    "release_date",
    "dataset_suffix",
    "source_revision",
    "previous_revision",
    "status",
    "available_at",
    "availability_evidence",
    "variants",
    "revision",
    "correction_reason",
    "notes_status",
    "no_changes_reason",
    "notes",
}


class UniqueLoader(yaml.SafeLoader):
    """Refuse duplicate YAML keys instead of silently losing a decision."""


def _unique_mapping(loader: UniqueLoader, node: yaml.MappingNode) -> dict:
    loader.flatten_mapping(node)
    pairs = []
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise ValueError("YAML mapping keys must be text")
        pairs.append((key, loader.construct_object(value_node)))
    return catalog.unique_object(pairs)


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def load_yaml(text: str) -> object:
    return yaml.load(text, Loader=UniqueLoader)


def git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo.resolve()}", "-C", str(repo), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"Git source read failed: {result.stderr.strip()}")
    return result.stdout


def check_revision(repo: Path, revision: str) -> None:
    if not isinstance(revision, str) or not catalog.SHA.fullmatch(revision):
        raise ValueError("source revisions must be full, lowercase Git commit SHAs")
    if git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").strip() != revision:
        raise ValueError("the revision must name the commit itself, not a tag")


def public_text(text: str, where: str) -> None:
    problems = rn.publication_problems(where, text, 1)
    if problems:
        raise ValueError("\n".join(problems))


@dataclass(frozen=True)
class Note:
    id: str
    audiences: list[str]
    title: str | None
    impact: str | None
    action: str | None
    details: str | None
    no_user_impact: str | None


def parse_note(text: str, where: str) -> Note | None:
    blocks = BLOCK.findall(text.replace("\r\n", "\n"))
    if not blocks:
        if "starr-user-note:" in text:
            raise ValueError(
                f"{where}: starr-user-note must be inside one //// comment block"
            )
        return None
    if len(blocks) != 1:
        raise ValueError(f"{where}: exactly one starr-user-note block is allowed")
    wrapper = catalog.require_keys(load_yaml(blocks[0]), {"starr-user-note"}, where)
    data = wrapper["starr-user-note"]
    if not isinstance(data, dict):
        raise ValueError(f"{where}: starr-user-note must be a mapping")
    common = {"schema_version", "id", "audiences"}
    if "no_user_impact" in data:
        catalog.require_keys(data, common | {"no_user_impact"}, where)
        catalog.require_reason(data["no_user_impact"], f"{where}: no_user_impact")
    else:
        fields = common | {"title", "impact", "action"}
        catalog.require_keys(
            data, fields | ({"details"} if "details" in data else set()), where
        )
        for field in ("title", "impact", "action"):
            catalog.require_text(data[field], f"{where}: {field}")
            public_text(data[field], f"{where}: {field}")
        if len(data["title"]) > 140 or "\n" in data["title"] or "\r" in data["title"]:
            raise ValueError(f"{where}: title must be one line, at most 140 characters")
        if data["impact"].strip().lower() in ("none", "none.", "n/a"):
            raise ValueError(
                f"{where}: use no_user_impact with a reason instead of an empty impact"
            )
        if "details" in data:
            public_text(
                catalog.require_text(data["details"], f"{where}: details"),
                f"{where}: details",
            )
        for field in ("impact", "action", "details"):
            for _, line, fenced in rn.numbered_lines(data.get(field, ""), 1):
                if not fenced and re.match(r"^[ ]{0,3}#{1,3}(?:\s|$)", line):
                    raise ValueError(
                        f"{where}: headings inside {field} must start at ####"
                    )
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError(f"{where}: unsupported note schema_version")
    if not isinstance(data["id"], str) or not catalog.NOTE_ID.fullmatch(data["id"]):
        raise ValueError(f"{where}: id must be a lowercase, hyphenated identifier")
    audiences = data["audiences"]
    if not isinstance(audiences, list) or not audiences:
        raise ValueError(f"{where}: audiences must be a nonempty list")
    for audience in audiences:
        catalog.require_text(audience, f"{where}: audience")
        if "\n" in audience or "\r" in audience:
            raise ValueError(f"{where}: audience must be one line")
        public_text(audience, f"{where}: audience")
    if len(set(audiences)) != len(audiences):
        raise ValueError(f"{where}: duplicate audience")
    note = Note(
        data["id"],
        audiences,
        data.get("title"),
        data.get("impact"),
        data.get("action"),
        data.get("details"),
        data.get("no_user_impact"),
    )
    if note.no_user_impact is None:
        _, problems = rn.check_entry("2000-01-01.qmd", render_note(note, "2000-01-01"))
        if problems:
            raise ValueError(f"{where}: " + "\n".join(problems))
    return note


def render_note(note: Note, date: str) -> str:
    if note.no_user_impact is not None:
        raise ValueError(f"{note.id}: a no-impact decision is not a public note")
    lines = [
        f"### {note.title} {{#{note.id}-{date}}}",
        "",
        "**What changed**",
        "",
        note.impact,
        "",
        "**Who is affected**",
        "",
        *[f"- {item}" for item in note.audiences],
        "",
        "**Action required**",
        "",
        note.action,
    ]
    if note.details:
        lines += ["", note.details]
    return "\n".join(line for line in lines if line is not None).rstrip() + "\n"


def candidates(repo: Path, previous: str, source: str) -> dict[str, str]:
    """Recover the last release-line version, including folded/deleted fragments."""
    check_revision(repo, previous)
    check_revision(repo, source)
    git(repo, "merge-base", "--is-ancestor", previous, source)
    if previous not in git(repo, "rev-list", "--first-parent", source).splitlines():
        raise ValueError(
            "previous_revision must be on the source revision's first-parent release history"
        )
    revisions = git(
        repo, "rev-list", "--first-parent", f"{previous}..{source}"
    ).splitlines()
    result = {}
    for revision in revisions:
        names = git(
            repo,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "--diff-filter=AM",
            "--no-renames",
            "-r",
            f"{revision}^",
            revision,
            "--",
            "CHANGELOG-*.adoc",
        ).splitlines()
        for name in names:
            if not FRAGMENT.fullmatch(name):
                raise ValueError(f"unsupported changelog path: {name}")
            result.setdefault(name, revision)
    return dict(sorted(result.items()))


def check_pr(repo: Path, base: str, head: str) -> None:
    check_revision(repo, base)
    check_revision(repo, head)
    base = git(repo, "merge-base", base, head).strip()
    changes = git(
        repo, "diff", "--name-status", "--no-renames", base, head, "--"
    ).splitlines()
    needs_note = False
    written = 0
    ids = set()
    for change in changes:
        status, path = change.split("\t", 1)
        needs_note |= path.startswith(USER_FACING_PATHS)
        if status == "D" or not FRAGMENT.fullmatch(path):
            continue
        text = git(repo, "show", f"{head}:{path}")
        note = parse_note(text, path)
        if note is None:
            raise ValueError(
                f"{path}: add a versioned user note or explicit no_user_impact reason"
            )
        if note.id in ids:
            raise ValueError(f"duplicate note id in this change: {note.id}")
        ids.add(note.id)
        before = (
            parse_note(git(repo, "show", f"{base}:{path}"), path)
            if status != "A"
            else None
        )
        written += note != before
    if needs_note and not written:
        raise ValueError(
            "data-product changes require a new/updated note or an explicit no_user_impact reason"
        )


def prepare_record(
    repo: Path, previous: str, source: str, date: str, variants: list[str]
) -> dict:
    snapshot = catalog.iso_date(date, "release_date")
    notes = []
    for path, revision in candidates(repo, previous, source).items():
        note = parse_note(git(repo, "show", f"{revision}:{path}"), path)
        if note is None:
            decision, reason = "unresolved", None
        elif note.no_user_impact:
            decision, reason = "omit", note.no_user_impact
        elif PRODUCT not in note.audiences:
            decision, reason = "omit", "Not part of the STARR OMOP 5.4 audience."
        else:
            decision, reason = "include", None
        notes.append({"path": path, "disposition": decision, "reason": reason})
    return {
        "schema_version": 1,
        "release_date": date,
        "dataset_suffix": "_" + snapshot.isoformat().replace("-", "_"),
        "source_revision": source,
        "previous_revision": previous,
        "status": "draft",
        "available_at": None,
        "availability_evidence": None,
        "variants": variants,
        "revision": 1,
        "correction_reason": None,
        "notes_status": "published"
        if any(item["disposition"] == "include" for item in notes)
        else "no-user-facing-changes",
        "no_changes_reason": None,
        "notes": notes,
    }


def export_bundle(repo: Path, value: object) -> dict:
    record = catalog.require_keys(value, RECORD_FIELDS, "upstream release record")
    date = catalog.iso_date(record["release_date"], "release_date").isoformat()
    source_notes = candidates(
        repo, record["previous_revision"], record["source_revision"]
    )
    decisions = record["notes"]
    if not isinstance(decisions, list):
        raise ValueError("notes must explicitly account for every release fragment")
    paths = set()
    selected = []
    for decision in decisions:
        fields = {"path", "disposition", "reason"}
        if isinstance(decision, dict) and "text_revision" in decision:
            fields.add("text_revision")
        catalog.require_keys(decision, fields, "note decision")
        path = decision["path"]
        if not isinstance(path, str) or path not in source_notes or path in paths:
            raise ValueError(f"duplicate or out-of-release fragment: {path}")
        paths.add(path)
        note = parse_note(git(repo, "show", f"{source_notes[path]}:{path}"), path)
        if "text_revision" in decision:
            if (
                type(record["revision"]) is not int
                or record["revision"] < 2
                or decision["disposition"] != "include"
            ):
                raise ValueError(
                    f"{path}: text_revision is only for an included historical correction"
                )
            catalog.require_reason(record["correction_reason"], "correction_reason")
            text_revision = decision["text_revision"]
            check_revision(repo, text_revision)
            corrected = parse_note(git(repo, "show", f"{text_revision}:{path}"), path)
            if corrected is None or (note is not None and corrected.id != note.id):
                raise ValueError(
                    f"{path}: the corrected note must preserve its original id"
                )
            note = corrected
        if decision["disposition"] == "include":
            if (
                note is None
                or note.no_user_impact is not None
                or PRODUCT not in note.audiences
            ):
                raise ValueError(
                    f"{path}: inclusion requires a complete OMOP user note"
                )
            if decision["reason"] is not None:
                raise ValueError(
                    f"{path}: included notes must not have an omission reason"
                )
            selected.append(note)
        elif decision["disposition"] == "omit":
            catalog.require_reason(decision["reason"], f"{path}: omission reason")
        else:
            raise ValueError(
                f"{path}: unresolved note; include it or record an explicit omission reason"
            )
    if paths != source_notes.keys():
        raise ValueError(
            f"unaccounted release fragments: {sorted(source_notes.keys() - paths)}"
        )
    selected.sort(key=lambda note: note.id)
    if record["notes_status"] == "published":
        if not selected or record["no_changes_reason"] is not None:
            raise ValueError(
                "published requires included notes and no no_changes_reason"
            )
        entry = "\n".join(render_note(note, date) for note in selected)
    elif record["notes_status"] == "no-user-facing-changes":
        if selected:
            raise ValueError("no-user-facing-changes conflicts with included notes")
        reason = catalog.require_reason(
            record["no_changes_reason"], "no_changes_reason"
        )
        entry = f"### No user-facing changes {{#no-user-facing-changes-{date}}}\n\n{reason.rstrip()}\n"
    else:
        raise ValueError("notes_status must be published or no-user-facing-changes")
    if record["status"] == "available":
        catalog.require_reason(record["availability_evidence"], "availability_evidence")
    elif record["availability_evidence"] is not None:
        raise ValueError("a draft must not claim availability_evidence")
    public_text(entry, f"{date}.qmd")
    _, problems = rn.check_entry(f"{date}.qmd", entry)
    if problems:
        raise ValueError("\n".join(problems))
    metadata = {
        key: record[key]
        for key in (
            "schema_version",
            "release_date",
            "dataset_suffix",
            "status",
            "source_revision",
            "available_at",
            "variants",
            "revision",
            "notes_status",
            "correction_reason",
        )
    }
    metadata.update(
        note_ids=[note.id for note in selected],
        entry_sha256=catalog.text_hash(entry),
        legacy=False,
    )
    release = catalog.parse_release(metadata, allow_draft=True)
    if release.correction_reason:
        public_text(release.correction_reason, "correction_reason")
    return {"release": release.to_dict(), "entry": entry}


def parse_bundle(value: object) -> tuple[catalog.Release, str]:
    data = catalog.require_keys(value, {"release", "entry"}, "release bundle")
    release = catalog.parse_release(data["release"])
    if release.legacy:
        raise ValueError("delivery cannot introduce legacy records")
    entry = catalog.require_text(data["entry"], "entry")
    if catalog.text_hash(entry) != release.entry_sha256:
        raise ValueError("bundle entry checksum does not match its release record")
    public_text(entry, f"{release.release_date}.qmd")
    _, problems = rn.check_entry(f"{release.release_date}.qmd", entry)
    if problems:
        raise ValueError("\n".join(problems))
    headings = [
        line
        for _, line, fenced in rn.numbered_lines(entry, 1)
        if not fenced and rn.CHANGE_LEVEL_HEADING.match(line)
    ]
    expected = (
        [f"{ident}-{release.release_date}" for ident in release.note_ids]
        if release.notes_status == "published"
        else [f"no-user-facing-changes-{release.release_date}"]
    )
    if len(headings) != len(expected) or any(
        not heading.endswith(f"{{#{ident}}}")
        for heading, ident in zip(headings, expected)
    ):
        raise ValueError(
            "entry headings must match the release's declared note_ids and outcome"
        )
    if release.correction_reason:
        public_text(release.correction_reason, "correction_reason")
    return release, entry


def check_evolution(old: catalog.Release | None, new: catalog.Release) -> None:
    if old is None:
        if new.revision != 1:
            raise ValueError("a new release must start at revision 1")
    elif new != old and new.revision != old.revision + 1:
        raise ValueError(
            f"{new.release_date}: historical changes require revision {old.revision + 1} "
            "and an explicit correction_reason"
        )


def current_umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def atomic_write(path: Path, content: str) -> None:
    """Replace ``path`` in one step, leaving it as a plain write would."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temp = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # A temporary file is readable by its owner only, and the sync runs as
        # root in Docker on a checkout the host reads: keep the destination's
        # mode, or give a new file the default one.
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            mode = 0o666 & ~current_umask()
        os.chmod(temp, mode)
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def sync_bundle(root: Path, value: object) -> bool:
    release, text = parse_bundle(value)
    releases = catalog.load_catalog(root)
    problems = catalog.entry_problems(root, releases)
    entries, entry_errors = rn.load_entries(root / "docs/release_notes")
    if problems or entry_errors:
        raise ValueError("\n".join(problems + entry_errors))
    old = next(
        (item for item in releases if item.release_date == release.release_date), None
    )
    check_evolution(old, release)
    record_path = root / catalog.CATALOG_DIR / f"{release.release_date}.json"
    entry_path = root / "docs/release_notes" / f"{release.release_date}.qmd"
    if old == release:
        return False
    incoming, problems = rn.check_entry(entry_path.name, text)
    if incoming is None:
        raise ValueError("\n".join(problems))
    entries = [item for item in entries if item.date != incoming.date] + [incoming]
    problems = rn.anchor_problems(entries)
    if problems:
        raise ValueError("\n".join(problems))
    before = entry_path.read_bytes().decode("utf-8") if entry_path.exists() else None
    atomic_write(entry_path, text)
    try:
        atomic_write(record_path, catalog.json_text(release.to_dict()))
    except OSError:
        if before is None:
            entry_path.unlink()
        else:
            atomic_write(entry_path, before)
        raise
    return True


def check_history(root: Path, base: str) -> None:
    check_revision(root, base)
    current = {record.release_date: record for record in catalog.load_catalog(root)}
    problems = catalog.entry_problems(root, list(current.values()))
    if problems:
        raise ValueError("\n".join(problems))
    old_paths = git(
        root, "ls-tree", "-r", "--name-only", base, "--", catalog.CATALOG_DIR
    ).splitlines()
    for path in old_paths:
        if not path.endswith(".json"):
            continue
        old = catalog.parse_release(
            json.loads(
                git(root, "show", f"{base}:{path}"),
                object_pairs_hook=catalog.unique_object,
            )
        )
        new = current.pop(old.release_date, None)
        if new is None:
            raise ValueError(
                f"{old.release_date}: historical release records cannot be removed"
            )
        check_evolution(old, new)
    for new in current.values():
        check_evolution(None, new)


def export_available(repo: Path, records_dir: Path, output_dir: Path) -> list[str]:
    """Validate all available records before writing any delivery artifacts."""
    bundles = {}
    if not records_dir.is_dir():
        raise ValueError(f"release record directory is missing: {records_dir}")
    for path in sorted(records_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        record = catalog.require_keys(
            load_yaml(path.read_text(encoding="utf-8")), RECORD_FIELDS, path.name
        )
        date = catalog.iso_date(record["release_date"], "release_date").isoformat()
        if path.stem != date:
            raise ValueError(f"{path.name}: filename must match release_date")
        if record["status"] == "draft":
            print(f"Draft {date}: not delivered", file=sys.stderr)
            continue
        bundle = export_bundle(repo, record)
        parse_bundle(bundle)
        bundles[date] = bundle
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(
            "delivery output directory must be empty (refusing stale artifacts)"
        )
    for date, bundle in bundles.items():
        atomic_write(output_dir / f"{date}.json", catalog.json_text(bundle))
    dates = list(bundles)
    atomic_write(output_dir / "dates.json", catalog.json_text(dates))
    return dates


def delivery_body(root: Path, date: str) -> str:
    """Fill the destination repository's PR template after delivery validation."""
    catalog.iso_date(date, "release_date")
    record = catalog.parse_release(
        catalog.read_json(root / catalog.CATALOG_DIR / f"{date}.json")
    )
    text = (root / ".github/pull_request_template.md").read_text(encoding="utf-8")
    additions = {
        "Summary": (
            f"Import the available `{record.dataset_suffix}` dataset release "
            f"(revision {record.revision}) and regenerate both release pages and LLM indexes. "
            "User-facing text was reviewed with the source change; no live PR text was scraped."
        ),
        "Related Issues": f"Dataset release: `{record.release_date}`.",
        "How to Verify": (
            "The delivery job ran these commands inside Docker:\n\n"
            "- `python scripts/generate_release_notes.py --check` - passed.\n"
            "- `python tests/test_release_notes.py` - passed.\n"
            "- `python tests/test_release_pipeline.py` - passed.\n"
            "- `python scripts/build_site.py --offline` - passed.\n\n"
            "The offline preview does not refresh private model metadata or execute FAQ queries. "
            "The production deployment runs both with its own credentials; it never falls back to the preview."
        ),
    }
    for heading, content in additions.items():
        marker = f"## {heading}\n"
        if marker not in text:
            raise ValueError(f"PR template is missing the {heading} section")
        text = text.replace(marker, marker + "\n" + content + "\n", 1)
    start = text.find("## Pages / Files Changed\n")
    end = text.find("\n## ", start + 1)
    if start < 0 or end < 0:
        raise ValueError("PR template is missing the Pages / Files Changed section")
    section = text[start:end]
    files = [
        f"{catalog.CATALOG_DIR}/{date}.json",
        f"docs/release_notes/{date}.qmd",
        "docs/release_notes.qmd",
        "docs/released_datasets.qmd",
        "docs/llms.txt",
        "docs/llms-full.txt",
    ]
    if "\n-\n" not in section:
        raise ValueError("PR template's file-list placeholder has changed")
    section = section.replace(
        "\n-\n", "\n" + "\n".join(f"- `{path}`" for path in files) + "\n", 1
    )
    text = text[:start] + section + text[end:]
    return re.sub(r"(?m)^- \[ \] (.*Release notes entry.*)$", r"- [x] \1", text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser(
        "check-pr", help="require a reviewed note or explicit no-impact decision"
    )
    check.add_argument("--repo", type=Path, required=True)
    check.add_argument("--base", required=True)
    check.add_argument("--head", required=True)
    prepare = sub.add_parser(
        "prepare", help="prepare an unpublished release record from fixed revisions"
    )
    prepare.add_argument("--repo", type=Path, required=True)
    prepare.add_argument("--previous", required=True)
    prepare.add_argument("--source", required=True)
    prepare.add_argument("--date", required=True)
    prepare.add_argument(
        "--variants", nargs="+", choices=catalog.VARIANTS, required=True
    )
    prepare.add_argument("--output", type=Path, required=True)
    export = sub.add_parser(
        "export", help="freeze a private release record into a public bundle"
    )
    export.add_argument("--repo", type=Path, required=True)
    export.add_argument("--record", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    batch = sub.add_parser(
        "export-available", help="validate and export available records only"
    )
    batch.add_argument("--repo", type=Path, required=True)
    batch.add_argument("--records-dir", type=Path, required=True)
    batch.add_argument("--output-dir", type=Path, required=True)
    sync = sub.add_parser(
        "sync", help="import an available release, without silent historical rewrites"
    )
    sync.add_argument("--bundle", type=Path, required=True)
    sync.add_argument("--root", type=Path, default=ROOT)
    history = sub.add_parser(
        "check-history", help="require versioned corrections to published records"
    )
    history.add_argument("--base", required=True)
    history.add_argument("--root", type=Path, default=ROOT)
    body = sub.add_parser(
        "delivery-body",
        help="fill the PR template after the delivery job's checks pass",
    )
    body.add_argument("--date", required=True)
    body.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check-pr":
            check_pr(args.repo, args.base, args.head)
            print("User-note contract passed")
        elif args.command == "prepare":
            if args.output.exists():
                raise ValueError(
                    f"{args.output}: refusing to overwrite an existing release record"
                )
            value = prepare_record(
                args.repo, args.previous, args.source, args.date, args.variants
            )
            atomic_write(
                args.output, yaml.safe_dump(value, sort_keys=False, allow_unicode=True)
            )
            count = sum(note["disposition"] == "unresolved" for note in value["notes"])
            print(
                f"Prepared DRAFT {args.date}; {count} unresolved notes. No dataset has been declared available."
            )
        elif args.command == "export":
            value = export_bundle(
                args.repo, load_yaml(args.record.read_text(encoding="utf-8"))
            )
            if args.record.stem != value["release"]["release_date"]:
                raise ValueError("release record filename must match release_date")
            atomic_write(args.output, catalog.json_text(value))
            print(
                f"Exported {value['release']['status']} release {value['release']['release_date']}"
            )
        elif args.command == "sync":
            changed = sync_bundle(args.root, catalog.read_json(args.bundle))
            print(
                "Release imported"
                if changed
                else "Release already matches; no files changed"
            )
        elif args.command == "export-available":
            dates = export_available(args.repo, args.records_dir, args.output_dir)
            print(f"Exported {len(dates)} available releases")
        elif args.command == "check-history":
            check_history(args.root, args.base)
            print("Release history changes are explicitly versioned")
        elif args.command == "delivery-body":
            atomic_write(args.output, delivery_body(ROOT, args.date))
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Release pipeline stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
