"""The public, versioned dataset records shared by both release pages."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
VARIANTS = ("core", "1pcent", "lite", "1pcent_lite")
PROJECT = "som-rit-phi-starr-prod"
DATASET_PREFIX = "starr_omop_cdm54_confidential"
CATALOG_DIR = "data/releases"
LEGACY_DATES = frozenset(("2026-01-18", "2026-06-08", "2026-09-10"))
SHA = re.compile(r"[0-9a-f]{40}")
NOTE_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


def long_date(date: dt.date) -> str:
    return f"{MONTHS[date.month - 1]} {date.day}, {date.year}"


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iso_date(value: object, field: str) -> dt.date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{field} must be a quoted YYYY-MM-DD date")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} is not a calendar date") from error


def require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")
    if value.strip().lower() in ("todo", "tbd", "replace_me", "placeholder"):
        raise ValueError(f"{field} still contains a placeholder")
    return value


def require_reason(value: object, field: str) -> str:
    text = require_text(value, field)
    if text.strip().lower() in ("none", "none.", "n/a", "na", "-"):
        raise ValueError(f"{field} must explain the decision, not just say None")
    return text


def require_keys(value: object, fields: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping")
    missing = fields - value.keys()
    extra = value.keys() - fields
    if missing or extra:
        raise ValueError(
            f"{where}: missing keys {sorted(missing)}; unknown keys "
            f"{sorted(str(key) for key in extra)}"
        )
    return value


@dataclass(frozen=True)
class Release:
    schema_version: int
    release_date: str
    dataset_suffix: str
    status: str
    source_revision: str | None
    available_at: str | None
    variants: list[str]
    revision: int
    notes_status: str
    note_ids: list[str]
    entry_sha256: str | None
    correction_reason: str | None
    legacy: bool

    @property
    def date(self) -> dt.date:
        return dt.date.fromisoformat(self.release_date)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_release(value: object, *, allow_draft: bool = False) -> Release:
    data = require_keys(value, set(Release.__dataclass_fields__), "release record")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported release schema_version (expected 1)")
    date = iso_date(data["release_date"], "release_date")
    if data["dataset_suffix"] != "_" + date.isoformat().replace("-", "_"):
        raise ValueError("dataset_suffix must match release_date")
    if data["status"] not in (
        ("available", "draft") if allow_draft else ("available",)
    ):
        raise ValueError("only an explicitly available dataset can be published")
    if type(data["legacy"]) is not bool:
        raise ValueError("legacy must be a boolean")
    if data["legacy"]:
        if data["release_date"] not in LEGACY_DATES:
            raise ValueError("only the three pre-catalog snapshots may be legacy")
        if data["source_revision"] is not None or data["available_at"] is not None:
            raise ValueError("legacy records must not invent release provenance")
    else:
        if not isinstance(data["source_revision"], str) or not SHA.fullmatch(
            data["source_revision"]
        ):
            raise ValueError("source_revision must be a full, lowercase Git commit SHA")
        if data["status"] == "available":
            available = require_text(data["available_at"], "available_at")
            try:
                stamp = dt.datetime.fromisoformat(available.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError(
                    "available_at must be an ISO-8601 timestamp"
                ) from error
            if stamp.tzinfo is None or stamp.date() < date:
                raise ValueError(
                    "available_at needs a timezone and cannot precede the snapshot"
                )
        elif data["available_at"] is not None:
            raise ValueError("a draft must not claim an available_at time")
    variants = data["variants"]
    if (
        not isinstance(variants, list)
        or not variants
        or any(not isinstance(item, str) or item not in VARIANTS for item in variants)
        or len(set(variants)) != len(variants)
    ):
        raise ValueError(f"variants must be a nonempty, unique subset of {VARIANTS}")
    if variants != [item for item in VARIANTS if item in variants]:
        raise ValueError(f"variants must use the canonical order {VARIANTS}")
    if type(data["revision"]) is not int or data["revision"] < 1:
        raise ValueError("revision must be a positive integer")
    if data["revision"] > 1:
        require_reason(data["correction_reason"], "correction_reason")
    elif data["correction_reason"] is not None:
        raise ValueError("revision 1 must not have a correction_reason")
    if data["notes_status"] not in (
        "published",
        "no-user-facing-changes",
        "not-recorded",
    ):
        raise ValueError("notes_status must explicitly describe the release's notes")
    ids = data["note_ids"]
    if (
        not isinstance(ids, list)
        or any(not isinstance(item, str) or not NOTE_ID.fullmatch(item) for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("note_ids must contain unique, lowercase note identifiers")
    if bool(ids) != (data["notes_status"] == "published"):
        raise ValueError(
            "published notes require note_ids; other outcomes must have none"
        )
    if data["notes_status"] == "not-recorded":
        if not data["legacy"] or data["entry_sha256"] is not None:
            raise ValueError(
                "not-recorded is reserved for legacy releases without an entry"
            )
    elif not isinstance(data["entry_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", data["entry_sha256"]
    ):
        raise ValueError("entry_sha256 must be the SHA-256 of the exact entry text")
    return Release(**data)


def load_catalog(root: Path) -> list[Release]:
    folder = root / CATALOG_DIR
    if not folder.is_dir():
        raise ValueError(
            f"{CATALOG_DIR} is missing; restore the committed release records"
        )
    releases = []
    for path in sorted(folder.iterdir()):
        if path.name.startswith((".", "_")):
            continue
        if not path.is_file() or path.suffix != ".json":
            raise ValueError(
                f"{path.name}: release records must be YYYY-MM-DD.json files"
            )
        record = parse_release(read_json(path))
        if path.name != f"{record.release_date}.json":
            raise ValueError(f"{path.name}: filename must match release_date")
        releases.append(record)
    if not releases:
        raise ValueError("the release catalog is empty")
    return sorted(releases, key=lambda item: item.date, reverse=True)


def entry_problems(root: Path, releases: list[Release]) -> list[str]:
    folder = root / "docs/release_notes"
    expected = {
        f"{record.release_date}.qmd": record
        for record in releases
        if record.notes_status != "not-recorded"
    }
    found = {
        path.name
        for path in folder.glob("*.qmd")
        if not path.name.startswith(("_", "."))
    }
    problems = [
        f"{name}: no available dataset record; synchronize its release bundle first"
        for name in sorted(found - expected.keys())
    ]
    problems += [
        f"{name}: release record is missing its entry"
        for name in sorted(expected.keys() - found)
    ]
    for name in sorted(found & expected.keys()):
        text = (folder / name).read_bytes().decode("utf-8")
        if text_hash(text) != expected[name].entry_sha256:
            problems.append(
                f"{name}: entry checksum differs; import a versioned correction"
            )
    return problems


def dataset_name(variant: str, suffix: str) -> str:
    part = "" if variant == "core" else f"_{variant}"
    return f"{DATASET_PREFIX}{part}{suffix}"


def dataset_link(variant: str, suffix: str) -> str:
    name = dataset_name(variant, suffix)
    return f"[`{PROJECT}.{name}`](https://console.cloud.google.com/bigquery?p={PROJECT}&d={name}&page=dataset)"


def render_datasets(releases: list[Release]) -> str:
    latest = {
        variant: next((item for item in releases if variant in item.variants), None)
        for variant in VARIANTS
    }
    lines = [
        "---",
        'title: "Released Datasets"',
        "execute:",
        "  echo: false",
        "---",
        "",
        "A reverse-chronological log of available STARR-OMOP dataset releases. "
        "The same release records supply this page and the [Release Notes](release_notes.qmd).",
        "",
        "::: {.callout-note}",
        "Each dataset name below links directly to the Google Cloud Console, pinned to that "
        "dataset in your active GCP session. Query costs are billed to your own active billing project.",
        ":::",
        "",
        "## Dataset Variants",
        "",
        "A release lists only the variants confirmed available:",
        "",
        "- **Core** (no suffix) - full PHI-scrubbed dataset: structured data plus clinical text and text-derived NLP concepts.",
        "- **`_1pcent`** - a random 1% sample of the core dataset, for testing and sandbox exploration.",
        "- **`_lite`** - structured data only; clinical text and NLP tables removed.",
        "- **`_1pcent_lite`** - a random 1% sample of the lite variant.",
        "",
        "## Latest Datasets",
        "",
        "These stable aliases roll forward when their variant is declared available. "
        "Pin a dated snapshot instead when an analysis must be reproducible.",
        "",
    ]
    for variant, record in latest.items():
        if record:
            lines.append(
                f"- {dataset_link(variant, '_latest')} - `{record.dataset_suffix}`"
            )
    lines += [
        "",
        "## Release History",
        "",
        "The dated snapshots below are immutable.",
        "",
    ]
    for record in releases:
        same_month = sum(
            (other.date.year, other.date.month) == (record.date.year, record.date.month)
            for other in releases
        )
        title = (
            long_date(record.date)
            if same_month > 1
            else f"{MONTHS[record.date.month - 1]} {record.date.year}"
        )
        lines += [
            f"### {title}",
            "",
            f"Snapshot cut on **{long_date(record.date)}** (`{record.dataset_suffix}`).",
            "",
        ]
        if record.notes_status == "not-recorded":
            lines += [
                "Release notes were not recorded for this historical snapshot.",
                "",
            ]
        else:
            lines += [
                f"[Read the release notes](release_notes.qmd#release-{record.release_date}).",
                "",
            ]
        lines += [
            f"- {dataset_link(variant, record.dataset_suffix)}"
            for variant in record.variants
        ]
        lines.append("")
    return "\n".join(lines)
