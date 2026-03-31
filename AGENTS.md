# pubify-pubs Development Notes

Keep this file limited to non-obvious repo conventions that are likely to save time or prevent mistakes.

`pubify-pubs` is the package-owned publication engine. Keep host-workspace concerns and publication-owned content out of this package.

## Package Boundaries

Package-owned:

- workspace discovery through `pubify.conf`
- publication discovery and validation
- CLI behavior and shell behavior
- export/build/runtime orchestration
- conservative mirror sync and diff logic
- pinned publication-data helpers
- publication bootstrap templates
- generic tests against temporary host workspaces

Host-owned:

- `pubify.conf`
- `papers/<publication-id>`
- `pub.yaml` content
- publication figures, helpers, and LaTeX source
- pinned and external scientific data
- host-specific integration tests

## Design Guidance

- Prefer workspace terminology over embedded-repo terminology.
- Keep imports and docs on the `pubify_pubs.*` namespace.
- Keep the CLI name `pubs` stable.
- New generic workflow behavior belongs here; publication-specific figure logic does not.
- Do not make this package depend on host-repo layout beyond the workspace config contract.
- Treat `tex/autofigures/` as framework-owned generated output, not as a generic asset directory.
- Keep the publication-facing API conservative. Prefer small explicit helpers over broad generic escape hatches.

## Publication-Facing Conventions To Preserve

- `figures.py` is the publication entrypoint.
- The manuscript is the publication-local LaTeX tree rooted at the `main_tex` entry in `pub.yaml`.
- `figures.py` is a manuscript-ordered entrypoint file.
- Order `@figure`, `@stat`, and `@table` methods by the first place their outputs are used in the manuscript, regardless of object type.
- Do not group figures, stats, and tables by type unless that still matches first-use order in the manuscript.
- Order loaders by the first place they are needed by the manuscript-ordered `@figure`, `@stat`, and `@table` methods below.
- Prefer `@data(...)` over `@external_data(...)` when the input should be pinned under the workspace `data_root`.
- `publication_data_path(...)` owns pinned publication-data path resolution and parent creation.
- Format-owned publication-data helpers should generally come in save/load pairs.
- Small publication-local helpers may live in `figures.py`; larger publication-specific helper sets belong in publication-local helper modules, not in the package.
- Large or repeated filenames may be lifted into top-level constants for readability.
- Section comments in `figures.py` are only needed for sections that actually exist.
- When section comments are used, prefer `# Figures` if there are no stats or tables, `# Figures & Stats` if stats are present without tables, and `# Figures, Stats & Tables` if tables are present.
- When several pinned filenames share a scientific basename, prefer one basename constant plus derived filenames.
- Thin named loaders are acceptable when they give the paper a clear dependency name.
- Prefer local nested helpers for repeated subpanel assembly within one figure.
- Pinned result loaders may load domain objects directly through the owning library when that is the intended plotting object.

## Docs And Tests Must Stay Aligned On

- full `export` clears stale generated files in `tex/autofigures/`
- targeted `export` stays incremental
- `build`, `build --update`, and `build --skipupdate` preserve their current meanings
- shell prompt, history, automatic pickup of publication changes, and shell `update` behavior remain part of the supported CLI contract
- generated figures remain one-way local-to-mirror delivery, separate from managed-source sync
- diff status names and meanings stay documented and tested together
- data pinning behavior and helper semantics stay documented and tested together

## Tracked Generated Artifacts

- `site/` is a tracked generated artifact used for hosted docs output.
- Refresh it with `./.conda/bin/mkdocs build --strict`.
- Do not hand-edit files under `site/`.
- The repo-local pre-commit hook rebuilds and stages `site/`.

## Important Tests

- `tests/test_cli.py` is the main workflow contract suite. It covers config loading, init, check, export, build, shell behavior, pinning, sync, and diff.
- `tests/test_export.py` is the focused export/config normalization suite.

## Verification

- Canonical package test command:
  - `./.conda/bin/pytest tests -q`
- Repo-local pre-commit hook:
  - `sh .githooks/pre-commit`
- Host integration check after workflow-contract changes:
  - `/Users/nelsonnunes/Library/CloudStorage/Dropbox/Projects/pubify-pubs/.conda/bin/pytest -o cache_dir=/tmp/pubify-pubs-pytest-cache ../girmos-aosims/tests/test_pubify_pubs_integration.py -q`
- Practical local verification sequence after nontrivial changes:
  - run the package test command
  - run the repo-local pre-commit hook when docs or package behavior changed
  - run the host integration command if you changed config, discovery, CLI semantics, templates, or public helper behavior
