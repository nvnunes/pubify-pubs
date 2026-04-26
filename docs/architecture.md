# Architecture

This document is the source of truth for `pubify-pubs` package boundaries,
public API and CLI contracts, generated-artifact ownership, and
publication-workflow rules that must remain stable.

## Shared Guidance

This repo adopts the shared guidance in:

- `astro-agents/guidance/agent-surface.md`
- `astro-agents/guidance/public-python-projects.md`
- `astro-agents/guidance/python-development.md`

Repo-local commands, package boundaries, persisted contracts, lifecycle rules,
and publication-workflow conventions in this document remain the source of
truth for this repo.

## Package Role

`pubify-pubs` is the LaTeX-oriented downstream publication engine layered on
the TeX-agnostic `pubify-data` runtime.

It adapts the upstream runtime into the downstream workflow around:

- workspace discovery through `pubify.yaml`
- publication path resolution, validation, and LaTeX readiness checks
- the `pubs` CLI, shell behavior, and downstream-only commands
- export, build, and runtime orchestration
- conservative mirror sync and diff logic, currently deferred on this branch
  while the sync implementation is reintroduced
- pinned publication-data helpers
- publication bootstrap templates, including the shared publications-root
  `AGENTS.md` scaffold
- generic tests against temporary host workspaces

It does not own publication content. A host workspace does.

## Workspace And Ownership Boundaries

Package-owned:

- the `pubify-pubs` section in `pubify.yaml`
- publication folders as workflow targets rather than as package-owned content
- package-managed TeX support files
- generated outputs under `papers/<publication-id>/data/tex-artifacts/`
- the local TeX symlink view for generated outputs

Host-owned:

- `pubify.yaml`
- `papers/<publication-id>`
- `pub.yaml` content
- publication figures, manuscript-local helpers, and LaTeX source
- pinned and external scientific data, including any filesystem redirect for
  `papers/<publication-id>/data`
- host-specific integration tests

The workspace contract is intentionally small and downstream-owned. `pubify-data`
loads shared config files, but `pubify-pubs` owns these roots under the
`pubify-pubs` section:

- `pubify-pubs.publications_root` points at host-owned publication directories
- pinned publication-local data resolves through
  `papers/<publication-id>/data/`
- package code must not depend on additional host-repo layout beyond that
  config contract; hosts that need data elsewhere should use a symlink, bind
  mount, or equivalent filesystem redirect for the publication `data/` path

## Public Package Surface

Keep the public surface conservative.

- Keep LaTeX/export imports and docs on the `pubify_pubs.*` namespace.
- Keep reusable authoring decorators on the upstream `pubify_data.*` namespace.
- Keep the CLI name `pubs` stable.
- Keep the CLI as a thin wrapper over the Python API.
- Compose neutral list/update command routing through `pubify_data.CommandRegistry`;
  keep build, preview, LaTeX snippet, sync, and shell-specific behavior here.
- Keep the public Python API intentionally small and explicit.
- New generic discovery, runtime, and neutral command behavior belongs upstream
  in `pubify-data`; LaTeX/export workflow behavior belongs here.
- Publication-specific figure logic does not belong in the package.
- Prefer small explicit helpers over broad generic escape hatches.
- Source publications declared in `pub.yaml` are code dependencies. A paper's
  `figures.py` owns remapping from `ctx.source("<source-id>")` to local
  figure/stat/table IDs; TeX-facing references should stay local.

Primary supported Python entrypoints are:

- `find_workspace_root(...)`
- `pubify_data.figure`, `pubify_data.stat`, `pubify_data.table`,
  `pubify_data.data`, `pubify_data.external_data`
- `publication_data_path(...)`
- `save_publication_data_npz(...)`
- `load_publication_data_npz(...)`
- `FigurePanel`
- `FigureResult`
- `panel(...)`
- `StatResult`
- `TableResult`
- `main(...)`

## Publication Workflow Contracts

Preserve these publication-facing conventions:

- `figures.py` is the publication entrypoint.
- The manuscript is the publication-local LaTeX tree rooted at the `main_tex`
  entry in `pub.yaml`.
- `figures.py` is a manuscript-ordered entrypoint file.
- Order `@figure`, `@stat`, and `@table` methods by the first place their
  outputs are used in the manuscript, regardless of object type.
- Do not group figures, stats, and tables by type unless that still matches
  first-use order in the manuscript.
- Order loaders by the first place they are needed by the manuscript-ordered
  `@figure`, `@stat`, and `@table` methods below.
- Prefer `@data(...)` over `@external_data(...)` when the input should be
  pinned under the publication-local `data/` root.
- `publication_data_path(...)` owns pinned publication-data path resolution and
  parent creation.
- Format-owned publication-data helpers should generally come in save/load
  pairs.
- Small publication-local helpers may live in `figures.py`; larger
  publication-specific helper sets belong in publication-local modules, not in
  the package.
- Large or repeated filenames may be lifted into top-level constants for
  readability.
- Section comments in `figures.py` are only needed for sections that actually
  exist.
- When section comments are used, prefer `# Figures` if there are no stats or
  tables, `# Figures & Stats` if stats are present without tables, and
  `# Figures, Stats & Tables` if tables are present.
- When several pinned filenames share a scientific basename, prefer one
  basename constant plus derived filenames.
- Thin named loaders are acceptable when they give the paper a clear dependency
  name.
- Prefer local nested helpers for repeated subpanel assembly within one figure.
- Pinned result loaders may load domain objects directly through the owning
  library when that is the intended plotting object.

## Generated Outputs And Lifecycle

Generated outputs remain framework-owned rather than generic asset directories.

- `data/tex-artifacts/autofigures/` is the canonical generated figure directory.
- `tex/autofigures` is a symlink view for LaTeX convenience.
- full `figure update` treats the canonical generated figure directory as an
  authoritative snapshot and clears stale generated files first.
- targeted `figure <figure-id> update` stays incremental.
- `data/tex-artifacts/autostats.tex` is rewritten as one authoritative snapshot
  during stat updates and exposed through `tex/autostats.tex`.
- `data/tex-artifacts/autotables.tex` is rewritten as one authoritative snapshot
  during table updates and exposed through `tex/autotables.tex`.
- generated figures remain one-way local-to-mirror delivery, separate from
  managed-source sync.

CLI lifecycle expectations that docs and tests must stay aligned on:

- `update` refreshes package-owned TeX support files, validates the publication
  definition, and regenerates figures, stats, and tables.
- `build [--clear]` validates and compiles the current TeX tree; it does not
  regenerate figures, stats, or tables.
- shell prompt, history, automatic pickup of publication changes, and shell
  `update` behavior remain part of the supported CLI contract.
- when sync is enabled, diff status names and meanings stay documented and
  tested together.
- while sync is deferred, `push`, `pull`, and `diff` fail with an explicit
  temporary-unavailable message rather than falling through as unsupported
  commands.
- data pinning behavior and helper semantics stay documented and tested
  together.

## Generated Repo Artifacts

- `site/` is a tracked generated artifact used for hosted docs output.
- Refresh it with `./.conda/bin/mkdocs build --strict`.
- Do not hand-edit files under `site/`.
