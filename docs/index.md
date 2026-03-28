# pubify-pubs Docs

`pubify-pubs` is a local-first publication workflow package built around `pubify-mpl`.

It is meant for host workspaces that keep publication content, publication-local TeX sources, and pinned inputs under version control, while the package owns the generic workflow around:

- workspace discovery through `pubify.conf`
- publication discovery and validation
- figure export into publication-local `tex/autofigures/`
- LaTeX builds against the publication-local `tex/` tree
- conservative mirror sync
- data pinning
- publication bootstrapping through the `pubs` CLI

This package does not own your publications. A host workspace does.

## Requirements

- Python 3.10+
- `pubify-mpl`
- a working LaTeX installation for `pubs <publication-id> build`

The build step runs `latexmk` against the publication-local TeX tree. If exported figures use LaTeX text rendering through `pubify-mpl`, LaTeX must also be available during Python-side export.

## Workspace Model

`pubify-pubs` treats a configured host workspace as the source of truth.

A workspace is rooted by `pubify.conf`:

```yaml
publications_root: papers
data_root: output/papers
```

The package discovers that file by walking upward from the current working directory.

The workspace contract is:

- `publications_root` points at publication folders owned by the host workspace
- `data_root` points at pinned publication-local data owned by the host workspace
- package code lives independently from both

Keeping publication content and pinned data separate is intentional. It lets host workspaces keep publication source trees readable while still giving the workflow a stable place to store pinned inputs.

## Publication Layout

A typical publication looks like:

```text
papers/<publication-id>/
  figures.py
  pub.yaml
  tex/
    main.tex
    autofigures/
    build/
```

Key files:

- `figures.py`
  - publication entrypoint
  - defines loaders with `@data(...)` or `@external_data(...)`
  - defines exported figure functions with `@figure`
- `pub.yaml`
  - publication-local workflow settings
  - controls `main_tex`, `mirror_root`, `external_data_roots`, `sync_excludes`, `pubify-mpl-template`, and `pubify-mpl-defaults`
- `tex/`
  - canonical local TeX tree for the publication
- `tex/autofigures/`
  - framework-owned generated figure directory
- `tex/build/`
  - local build output directory

## Quick Start

Initialize a new publication from a workspace root:

```bash
pubs init my-paper
```

Then iterate with:

```bash
pubs my-paper check
pubs my-paper export
pubs my-paper build --export-if-stale
```

That creates a minimal publication skeleton and installs package-owned support files into the publication-local TeX tree.

## Typical Workflow

1. Keep publication-local TeX sources under `papers/<publication-id>/tex/`.
2. Define loaders and figure functions in `figures.py`.
3. Run `pubs <publication-id> check` to load and validate the publication definition.
4. Run `pubs <publication-id> export` to regenerate `tex/autofigures/`.
5. Run `pubs <publication-id> build` to compile the publication.
6. If a synced mirror is configured, use `diff`, `push`, or `pull` as needed.
7. When an external loader input should become publication-local and reproducible, pin it with `pubs <publication-id> data <loader-id> pin`.

## Figures And Loaders

Prefer `@data(...)` for pinned publication-local inputs under the configured workspace `data_root`. Use `@external_data(...)` only for explicit external roots declared in `pub.yaml`.

Both data decorators require relative paths. They reject absolute paths and path traversal.

Host publications import from the extracted package namespace directly:

```python
from pubify_pubs.data import (
    load_publication_data_npz,
    publication_data_path,
    save_publication_data_npz,
)
from pubify_pubs.decorators import data, external_data, figure
from pubify_pubs.export import FigureExport, panel
```

`@figure` marks a callable as a logical publication figure. Exported figure functions may return:

- a Matplotlib `Figure`
- a Matplotlib `Axes`
- a sequence of figures or axes
- a `FigureExport` value for explicit multi-panel control

## Pinned Publication Data

`pubify-pubs` includes helpers for publication-owned binary data:

- `publication_data_path(...)`
- `save_publication_data_npz(...)`
- `load_publication_data_npz(...)`

These helpers resolve data under:

```text
<data_root>/<publication-id>/...
```

They are intentionally small and explicit. Format-owned helpers should generally come in save/load pairs when the package owns the format handling.

## Generated Figures And TeX Assets

`tex/autofigures/` is the framework-owned generated figure directory.

- full `export` treats it as an authoritative snapshot and clears stale generated files first
- targeted `export` stays incremental
- TeX should reference generated figures explicitly by path such as `autofigures/<name>.pdf`

Manual and static publication assets remain ordinary publication-local TeX files. They do not belong in `tex/autofigures/`.

## Mirror Sync Model

The local publication TeX tree is canonical.

Managed source files are publication-local TeX sources under `tex/`, excluding:

- generated figures in `tex/autofigures/`
- build artifacts in `tex/build/`
- publication-local sync exclusions from `pub.yaml`

Generated figures are delivered one-way from local `tex/autofigures/` to mirror `autofigures/`. They are not part of the hash-managed source sync model.

The mirror commands are conservative by design:

- `diff` compares local, mirror, and last-synced state
- `push` updates managed mirror files from local state
- `pull` updates managed local files from mirror state without deletions

## CLI

The installed command is `pubs`.

Top-level commands:

- `pubs list`
- `pubs init <publication-id>`

Publication commands:

- `pubs <publication-id> check`
- `pubs <publication-id> shell`
- `pubs <publication-id> export [<figure-id> [<subfig-idx>]]`
- `pubs <publication-id> data [list]`
- `pubs <publication-id> data <loader-id> pin`
- `pubs <publication-id> figure [list]`
- `pubs <publication-id> ignore <relative-path>`
- `pubs <publication-id> build [--export|--export-if-stale]`
- `pubs <publication-id> push [--force]`
- `pubs <publication-id> pull [--force]`
- `pubs <publication-id> diff [list|<relative-path>]`

The shell command opens a publication-scoped interactive session with command history and reload behavior for `figures.py`, `pub.yaml`, and publication-local helpers.

## Python API

The public Python API is intentionally small.

Primary entrypoints:

- `find_workspace_root(...)`
- `figure`, `data`, `external_data`
- `publication_data_path(...)`
- `save_publication_data_npz(...)`
- `load_publication_data_npz(...)`
- `FigureExport`
- `FigurePanel`
- `panel(...)`

See the [API reference](api.md) for the docstring-driven reference pages.
