# pubify-data Split Plan

This is a transitional execution plan for finishing the split between
`pubify-data` and `pubify-pubs`.

It is intentionally not a release plan. Release timing, packaging, tags, and
publication decisions are out of scope until explicitly decided.

## Current Checkpoint

- `pubify-data` exists as a peer public Python project on the `develop` branch.
  - Path:
    `/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/pubify-data`
- `pubify-data` currently provides an initial neutral runtime surface:
  decorators, config section loading, discovery, neutral figure/stat/table
  models, loader execution, data helpers, and an explicit CLI command registry.
- `pubify-pubs` is on the `develop` branch and depends on `pubify-data`.
  - Path:
    `/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/pubify-pubs`
- `pubify-pubs` reads roots from the `pubify-pubs` section of `pubify.yaml`.
- New `pubify-pubs` stubs import authoring decorators from `pubify_data`.
- `pubify-pubs` still mostly uses its internal discovery, runtime, result
  models, and CLI lifecycle. That duplicated ownership is the main remaining
  work.
- Local verification currently requires `PYTHONPATH=../pubify-data/src` when
  commands are run from the `pubify-pubs` repo, because `pubify-data` is not
  installed into the local environment.
- The current checkpoint is staged in both repos but may not be committed. A
  new thread should check `git status --short` in both repos before changing
  anything.

## Current Implementation Notes

`pubify-data` currently contains these first-pass modules:

- `src/pubify_data/config.py`
  - owns generic `pubify.yaml` discovery and raw namespaced-section loading.
  - does not own downstream root names such as `publications_root` or
    `data_root`.
- `src/pubify_data/decorators.py`
  - owns `data`, `external_data`, `figure`, `stat`, and `table`.
  - currently sets both `__pubify_data_*__` and compatibility `__pubs_*__`
    metadata.
- `src/pubify_data/discovery.py`
  - imports downstream-supplied entrypoints and discovers decorated loaders,
    figures, stats, and tables.
  - still accepts `__pubs_*__` metadata for compatibility.
- `src/pubify_data/runtime.py`
  - resolves loaders, caches loader outputs, captures user-code output, and
    runs neutral figures, stats, and tables.
  - currently uses loose object attributes on `publication.paths` and
    `publication.config`; Phase 1 must replace that with explicit adapter
    contracts.
- `src/pubify_data/figures.py`, `stats.py`, and `tables.py`
  - hold first-pass neutral result models.
  - these are intentionally not TeX-aware.
- `src/pubify_data/cli.py`
  - holds a first-pass explicit `CommandRegistry` and core list/update command
    handlers.
  - `pubify-pubs` does not yet compose this registry.
- `tests/test_core.py`
  - contains only a small smoke suite. Phase 1 must expand this into real
    contract coverage.

`pubify-pubs` currently still owns these generic behaviors that should move or
be delegated during later phases:

- `src/pubify_pubs/discovery.py`
  - still owns publication path resolution, entrypoint import, decorator
    discovery, and static dependency validation.
  - it now recognizes `pubify_data` decorator metadata, but does not delegate to
    `pubify_data.discovery`.
- `src/pubify_pubs/runtime.py`
  - still owns loader execution, figure/stat/table execution, output writing,
    `pubify-mpl` rc context, LaTeX build, generated-output staleness, and
    clearing output directories.
  - later phases should split this into a `pubify_data` runtime call plus
    `pubify-pubs` LaTeX adapters.
- `src/pubify_pubs/stats.py`
  - still owns TeX macro naming, TeX-ish display normalization, uniqueness, and
    `autostats.tex` rendering.
  - in the final split, neutral stat normalization belongs upstream and TeX
    macro rendering stays downstream.
- `src/pubify_pubs/tables.py`
  - still owns table normalization, LaTeX escaping, macro rendering,
    `autotables.tex`, and manuscript table reference checks.
  - in the final split, neutral rectangular table normalization belongs
    upstream; TeX rendering and manuscript checks stay downstream.
- `src/pubify_pubs/export.py`
  - still owns `FigureExport`, panel handling, Matplotlib/pubify-mpl export,
    filename generation, and figure source closing.
  - this should remain downstream, but it must adapt from upstream
    `FigureResult` cleanly.
- `src/pubify_pubs/commands/core.py` and `src/pubify_pubs/cli.py`
  - still own most list/update command flow, shell behavior, status reporting,
    build, preview, sync, and LaTeX snippet commands.
  - Phase 3 should make the neutral list/update flow compose
    `pubify_data.CommandRegistry` while keeping downstream-only commands here.

Current `pubify-pubs` changes already made:

- `pyproject.toml` depends on `pubify-data>=0.1.0`.
- `src/pubify_pubs/config.py` reads roots from:

```yaml
pubify-pubs:
  publications_root: papers
  data_root: output/papers
```

- `src/pubify_pubs/config.py` requires roots under the `pubify-pubs` section;
  legacy top-level `publications_root` and `data_root` are no longer accepted.
- `src/pubify_pubs/assets/init/figures.py` imports decorators from
  `pubify_data`.
- `src/pubify_pubs/stubs.py` adds new decorators from `pubify_data`.
- `docs/api.md` references decorator docs from `pubify_data`.
- `site/` was rebuilt by the repo-local pre-commit hook and is staged.

Recommended immediate next action for a new thread:

1. Confirm both repos are on `develop`.
2. Confirm staged checkpoint state with `git status --short` in both repos.
3. Commit or otherwise intentionally checkpoint the current baseline in both
   repos before Phase 1.
4. Start Phase 1 in `pubify-data`; do not change `pubify-pubs` runtime usage
   until the upstream adapter contracts and tests are hardened.

## Phase 0: Stabilize Current Checkpoint

- Keep the current staged `pubify-data` and `pubify-pubs` changes as the
  baseline.
- Verify both repos from a clean command sequence:
  - `pubify-data`: `PYTHONPATH=src pytest tests -q`
  - `pubify-data`: `PYTHONPATH=src mkdocs build --strict`
  - `pubify-pubs`: `PYTHONPATH=../pubify-data/src ./.conda/bin/pytest tests -q`
  - `pubify-pubs`: `PYTHONPATH=../pubify-data/src sh .githooks/pre-commit`
- Do not start deeper refactors until the staged baseline is committed or
  otherwise intentionally checkpointed.

## Phase 1: Harden pubify-data Public Contracts

- Replace loose object-based adapter assumptions with explicit public types:
  - `WorkspaceAdapter`
  - `PublicationAdapter`
  - `ArtifactWriter`
  - `RunContext`
  - `CommandRegistry`
- Keep downstream roots out of `pubify-data`; adapters must provide publication
  root, entrypoint path, data root, external roots, and artifact persistence.
- Make neutral result models final enough for downstream use:
  - `FigureResult` stores panel payloads and metadata only.
  - `ComputedStat` stores stat id plus keyed string values, with no TeX macro
    names.
  - `ComputedTable` stores normalized bodies and metadata, with no escaping or
    TeX rendering.
- Add contract tests for custom downstream root layouts, missing loaders,
  loader cache behavior, dynamic user-code errors, and command registry
  dispatch.

## Phase 2: Move pubify-pubs Onto pubify-data Runtime

- Refactor `pubify-pubs` discovery to call
  `pubify_data.load_publication_from_entrypoint(...)` through a
  `PubifyPubsPublicationAdapter`.
- Refactor loader resolution and `run_stats`/`run_tables` to use `pubify_data`
  runtime outputs first, then convert them through downstream LaTeX adapters.
- Refactor figures so `pubify_data.FigureResult` is accepted as the neutral
  core result and `pubify_pubs.FigureExport` becomes a LaTeX/pubify-mpl adapter
  payload.
- Keep `pubify-pubs` responsible for:
  - `tex/autofigures`
  - `autostats.tex`
  - `autotables.tex`
  - table reference checks
  - `pubify-mpl` rc context and export
  - LaTeX build diagnostics

## Phase 3: Move Core CLI Flow Upstream

- Make `pubify-pubs` compose the `pubify_data.CommandRegistry` instead of
  duplicating list/update lifecycle logic.
- `pubify-data` owns reusable neutral commands:
  - `data list`
  - `figure list`
  - `figure update`
  - `stat list`
  - `stat update`
  - `table list`
  - `table update`
  - `update`
- `pubify-pubs` registers downstream-only extensions:
  - `build`
  - `preview`
  - sync/diff
  - `figure/stat/table ... latex`
  - shell-specific LaTeX/build behavior
- Preserve current `pubs` CLI output unless a test is intentionally updated for
  the hard migration.

## Phase 4: Complete The Hard Migration

- Public authoring re-export shims from `pubify-pubs` have been removed.
- Tests and templates now require publication authoring imports to use:

```python
from pubify_data import data, external_data, figure, stat, table
```

- Keep `pubify_pubs` imports only for downstream LaTeX/export helpers:

```python
from pubify_pubs import FigureExport, TableResult
```

- Legacy top-level `pubify.yaml` root fallback has been removed; workspace
  roots must be declared as:

```yaml
pubify-pubs:
  publications_root: papers
  data_root: output/papers
```

- Migration notes mark the import/config changes as breaking.

## Phase 5: Docs And Integration

- Expand `pubify-data` docs to explain downstream adapter implementation and
  reusable CLI composition.
- Update `pubify-pubs` docs to describe itself as the LaTeX downstream of
  `pubify-data`, not the owner of generic runtime behavior.
- Run final verification:
  - both package test suites
  - both docs builds
  - `pubify-pubs` pre-commit hook
  - host integration test:
    `/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/pubify-pubs/.conda/bin/pytest -o cache_dir=/tmp/pubify-pubs-pytest-cache ../girmos-aosims/tests/test_pubify_pubs_integration.py -q`

## Acceptance Criteria

- `pubify-pubs` no longer owns duplicated generic discovery/runtime/CLI
  lifecycle code.
- `pubify-data` has no LaTeX, `pubify-mpl`, `tex/`, `latexmk`, `autofigures`,
  `autostats`, or `autotables` assumptions.
- Downstream roots are fully owned by `pubify-pubs` under `pubify-pubs:` in
  `pubify.yaml`.
- New publication authoring code imports decorators from `pubify_data`.
- Existing `pubs` LaTeX workflows still pass tests after migration.
- Another downstream package could reuse `pubify-data` with different roots and
  its own CLI executable without depending on `pubify-pubs`.

## Assumptions

- `pubify-data` remains a peer public Python project, not a subpackage inside
  `pubify-pubs`.
- `pubify-data` provides a CLI framework but no installed console script.
- The final split is a breaking migration for `pubify-pubs`.
- Temporary compatibility paths are allowed only during intermediate phases, not
  in the final finished state.
