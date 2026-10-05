# Authoring and Delivering Release Notes

**Write once with the code change, collect at dataset availability, render at site
build.** This folder retains one fixed `.qmd` entry per snapshot. Its matching
record in `data/releases/YYYY-MM-DD.json` supplies the release date, suffix,
available variants, provenance, and entry checksum.

`docs/release_notes.qmd` and `docs/released_datasets.qmd` are both generated.
Do not edit either page, or an imported entry, by hand.

## How the Page Is Organized

Releases are newest first: `# Year`, `## Month Year`, then `### Change`.
The index and table of contents link years and releases; two snapshots in a month
have distinct dates in the index. Quarto search indexes each release separately.
The existing expandable years/months, Expand all / Collapse all, deep links,
keyboard controls, print view, and no-JavaScript fallback are unchanged.

## Write and Review the Note Upstream

In starr-data-lake, put this block inside the existing changelog fragment:

```adoc
////
starr-user-note:
  schema_version: 1
  id: star-12345
  audiences:
    - STARR OMOP 5.4
  title: Observation periods include notes
  impact: |
    A note-only encounter now contributes to `observation_period`.
  action: |
    Recompute cohorts if their inclusion depends on observation period coverage.
////
```

`details` is optional Markdown, for example a field table or synthetic example.
Keep the title to one line and at most 140 characters. Subheadings in the text
start at `####`. The tool supplies `###` headings and release-qualified anchors,
so the same ticket can appear in different snapshots without duplicate IDs.

For a change with no user-facing impact, keep `schema_version`, `id`, and
`audiences`, but replace the public text fields with `no_user_impact: "<reason>"`.
Link the fragment from the PR instead of copying the text into its description.
The author and code reviewer approve the content with the implementation; the
pipeline does not rewrite it or require a second monthly editorial pass.

The upstream PR check covers `dbt/`, `src/`, and `deployments/` changes and all
added/edited changelog fragments. It requires a new or updated note or explicit
no-impact reason. An absent Docs tag no longer drops a change silently.
`STARR OMOP 5.4` in `audiences` identifies this site's eligible notes. An audience
for STARR Common can share the block, but Common-site delivery is outside this
OMOP workflow.

## Freeze an Identified Dataset Release

The shared `scripts/release_pipeline.py prepare` command creates an upstream
`releases/omop/YYYY-MM-DD.yaml` draft from explicit previous and deployed source
commits. It recovers fragments even after a changelog fold deletes them. Source
revisions must be full commit SHAs on the release's first-parent history; a
moving `main`, PR merge time, or changelog section date is not release identity.

The record contains:

| Field | Meaning |
|---|---|
| `schema_version` | `1` |
| `release_date`, `dataset_suffix` | Matching snapshot identity, e.g. `2026-09-10` and `_2026_09_10` |
| `previous_revision`, `source_revision` | Exact previous and newly deployed code revisions |
| `status` | `draft` or explicitly `available` |
| `available_at`, `availability_evidence` | Timezone-qualified availability timestamp and private deployment/run evidence |
| `variants` | Available subset of `core`, `1pcent`, `lite`, `1pcent_lite`, in that order |
| `revision`, `correction_reason` | Starts at `1` with no correction; later revisions explain the change |
| `notes_status`, `no_changes_reason` | `published`, or explicit `no-user-facing-changes` with a public explanation |
| `notes` | Every fragment, with `path`, `disposition` (`include`/`omit`), and an omission `reason` when applicable |

Prepared legacy/missing-note decisions remain `unresolved`, never silently
excluded. Export rejects missing, duplicate, foreign, or unresolved decisions.
An explicit omission can account for a non-shipped feature, unrelated audience,
or no-impact change; it must explain the decision. Legacy text cannot be
published by guessing a missing impact statement.

Declare availability only after the listed snapshots are accessible and their
`_latest` aliases have moved. The assertion comes from the release owner or a
verified deployment-completion step. **A configured deployment, merged PR,
GitHub release/tag, or built image is not that event.** The tool validates the
record but does not probe BigQuery or prove the availability assertion.

`export` freezes public metadata and note text into a JSON bundle. Private
evidence, omission reasons, fragment paths, and raw PR bodies are not exported.
Draft bundles can be inspected but are refused by the importer.

## Import and Build in Docker

From the docs repository root:

```bash
docker build -f Dockerfile.site -t starr-docs-site .
docker run --rm --network none \
  -v "$PWD:/workspace" -v /path/to/public-bundles:/bundles:ro \
  starr-docs-site python scripts/build_site.py \
  --bundle /bundles/YYYY-MM-DD.json --offline
```

This is the single synchronization-and-compilation command. It fails before
rendering if the bundle is absent, invalid, not available, or inconsistent.
Without `--bundle`, it renders committed releases only. Offline mode explicitly
uses committed model pages and skips FAQ execution; production never uses that
fallback.

For an import without rendering, use `release_pipeline.py sync --bundle ...`.
`generate_release_notes.py --check` validates the entire committed catalog and
entries without writing pages. The pre-render hook generates both release pages
from the same validated records, before the LLM indexes.

Identical imports are no-ops, including file timestamps. Changed historical
content needs exactly the next `revision` and a `correction_reason`. Checksums,
global anchor validation, and pre-write validation prevent accidental partial
or inconsistent delivery. The PR check also compares history with its base and
rejects removed release records or unversioned corrections.

For a text-only correction, commit the corrected note at the same fragment path
and pin that full commit SHA in the included decision's optional `text_revision`.
This override is accepted only for revision 2 or later with a correction reason,
and preserves the original note ID. Keep `source_revision` at the code that
actually built the dataset; a later editorial commit did not rebuild that data.
The override is private provenance and is not copied into the public bundle.

The initial January, June, and September 2026 records are the only permitted
legacy records. No exact deployed revision or availability timestamp was proven
for them, so those fields are explicitly null. September's existing entry is
unchanged. January and June say notes were not recorded, not "no changes."
The sample October entry in the earlier task artifacts is not an available
dataset and is not installed in the production catalog.

## Publication Checks

The build refuses invalid filenames/dates, missing dataset records or entries,
checksum mismatches, duplicate anchors, invalid front matter, and headings that
escape their release section. Each entry needs at least one unfenced `###` change
heading. Explicit IDs in titles, summaries, and bodies are checked together.
The build also refuses template comments, raw HTML, executable Quarto
cells/shortcodes, and private source-system links.

Identifier checks scan raw and normalized Markdown, including wrapped labels,
inline formatting, adjacent table cells, table header/value columns, and code
examples. Errors name source lines without repeating identifier values.
These checks are **backstops, not a guarantee that arbitrary prose is PHI-free**.
Authors and reviewers must still use public-facing text and synthetic examples.

## Automation Activation

The shared tooling must land in this repository before the upstream workflows,
which check out its `main`. The upstream integration supplies the changelog/PR
templates, a required user-note check, an availability-record template, and a
release-delivery workflow.

In **starr-data-lake**, install a GitHub App on `starr-docs-omop` with Contents
and Pull requests write permission. Set `RELEASE_DOCS_APP_ID` (Actions variable)
and `RELEASE_DOCS_APP_PRIVATE_KEY` (Actions secret). Make
`Validate versioned user notes` a required PR check.

Delivery starts when an available record reaches upstream `main`, or by explicit
workflow dispatch. It creates/updates `automation/omop-release-YYYY-MM-DD`, imports
only the public bundle, runs the offline build, and fills this repository's actual
PR template. Using an App token allows the generated PR to trigger this site's CI.
For no additional editorial review, enable auto-merge, protect `main` with
`Verify documentation`, and set upstream `RELEASE_DOCS_AUTOMERGE=true`. This
requests normal auto-merge; it never bypasses branch rules. If several release
PRs were prepared against the same base, rerun delivery after merging one to
refresh the others' generated pages.

In **starr-docs-omop**, configure:

| Setting | Purpose |
|---|---|
| Pages source: GitHub Actions | Permits `deploy-pages`; preserves the existing Quarto site-path configuration |
| `github-pages` environment | Limits production deployments to `main`; configure its normal approval rules |
| `STARR_READ_APP_ID` variable | Separate read-only GitHub App installed on starr-data-lake |
| `STARR_READ_APP_PRIVATE_KEY` secret | Key for that read-only App; never mounted into the build |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` variable | Google federation provider trusted for this repository/environment |
| `GCP_SERVICE_ACCOUNT` variable | Least-privilege account allowed to run the existing FAQ queries and read their training dataset |

The Documentation workflow runs credential-free PR checks and a distinct,
credentialed production build after merge to `main`. A supplied read-only source
checkout lets both existing model generators use the same source without embedding
GitHub credentials in Docker. Google credentials are ephemeral and mounted only
for that production render. Source model refresh and FAQ execution remain enabled.
Only a completed production render is uploaded to Pages.

Missing settings or any synchronization/build error fail visibly. No App
installation, secret, branch rule, Pages setting, or deployment is created merely
by adding these files; those administrative activation steps must be completed
in the repository settings.
