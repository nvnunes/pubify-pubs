# pubify-pubs

`pubify-pubs` is a local-first publication workflow package built around `pubify-mpl`.

It is meant for host workspaces that keep publications, publication-local TeX sources, and pinned inputs under version control, while the package owns the workflow around publication discovery, figure export, LaTeX builds, conservative mirror sync, data pinning, and publication bootstrapping.

This package does not own your publications. A host workspace does.

## Requirements

- Python 3.10+
- `pubify-mpl`
- a working LaTeX installation for `pubs <publication-id> build`

The `build` command runs `latexmk` against the publication-local TeX tree. If you export figures that use LaTeX text rendering through `pubify-mpl`, your TeX installation also needs to be available during Python-side figure export.

## How It Works

`pubify-pubs` treats a configured host workspace as the source of truth.

- `pubify.conf` defines where publications live and where pinned publication data is stored
- each publication lives under `papers/<publication-id>/`
- `figures.py` declares loaders and figures
- generated figures are exported into `tex/autofigures/`
- LaTeX builds run against the publication-local `tex/` tree
- optional mirror sync moves the canonical local TeX tree to and from a separately synced mirror directory

The local publication tree is canonical. Mirror sync is conservative and stateful rather than a blind directory copy.

## Quick Start

Create a workspace rooted by `pubify.conf`:

```yaml
publications_root: papers
data_root: output/papers
preview:
  publication: preview
  figure: preview
```

Initialize a new publication:

```bash
pubs init my-paper
```

That creates a publication skeleton like:

```text
papers/my-paper/
  figures.py
  pub.yaml
  tex/
    main.tex
    autofigures/
    build/
```

Then iterate with:

```bash
pubs my-paper check
pubs my-paper export
pubs my-paper build --export-if-stale
```

## Workspace Model

A host workspace is rooted by `pubify.conf`. The package discovers that file by walking upward from the current working directory.

`publications_root` contains publication directories. `data_root` contains pinned publication-local data, typically under:

```text
output/papers/<publication-id>/...
```

This separation is intentional:

- publications stay under the host workspace's configured publication root
- pinned data stays under the configured data root
- package code lives independently from both

`pubify.conf` can also configure preview backends independently for publication PDFs and exported figure PDFs:

```yaml
preview:
  publication: vscode
  figure: preview
```

Supported backend values are:

- `preview`
  - opens PDFs in macOS Preview via `open -a Preview`
- `vscode`
  - opens PDFs in a separate VS Code window via `code -n`

If the `preview` section is omitted, both commands default to the `preview` backend.

## Publication Layout

A typical publication contains:

```text
papers/<publication-id>/
  figures.py
  pub.yaml
  tex/
    main.tex
    autofigures/
    build/
```

`pub.yaml` owns publication-local settings such as:

- `main_tex`
- `mirror_root`
- `external_data_roots`
- `sync_excludes`
- `pubify-mpl-template`
- `pubify-mpl-defaults`

`figures.py` is the publication entrypoint. It defines:

- loaders decorated with `@data(...)` or `@external_data(...)`
- plotters decorated with `@figure`

## Typical Workflow

1. Keep publication-local TeX sources under `papers/<publication-id>/tex/`.
2. Define loaders and figure functions in `figures.py`.
3. Run `pubs <publication-id> check` to load and validate the publication definition.
4. Run `pubs <publication-id> export` to regenerate `tex/autofigures/`.
5. Run `pubs <publication-id> build` to compile the publication.
6. If you use a synced mirror such as a locally mounted Overleaf tree, run `diff`, `push`, or `pull` as needed.
7. When an external loader input should become publication-local and reproducible, pin it with `pubs <publication-id> data <loader-id> pin`.

## Figures And Loaders

Prefer `@data(...)` for pinned publication-local inputs under the configured `data_root`. Use `@external_data(...)` only for explicit external roots declared in `pub.yaml`.

Host publications import from the extracted package namespace directly:

```python
from pubify_pubs.data import load_publication_data_npz, publication_data_path, save_publication_data_npz
from pubify_pubs.decorators import data, external_data, figure
from pubify_pubs.export import FigureExport, panel
```

`@data(...)` and `@external_data(...)` both require relative paths. They reject absolute paths and path traversal.

`@figure` marks a callable as a logical publication figure. Exported figure functions typically return `FigureExport` values built from one or more panels.

`FigureExport` also exposes first-class caption sizing fields for `pubify-mpl`:

- `layout`
  - optional explicit layout override
  - when omitted, `pubify-pubs` uses the publication default layout from `pub.yaml`
- `caption_lines`
  - estimated line count for the main figure caption
- `subcaption_lines`
  - default estimated line count for panel subcaptions

If panels differ materially, `panel(..., subcaption_lines=...)` can override the figure-level subcaption count per panel.

## Pinned Publication Data

`pubify-pubs` includes helpers for publication-owned binary data:

- `publication_data_path(...)`
- `save_publication_data_npz(...)`
- `load_publication_data_npz(...)`

`publication_data_path(...)` resolves paths under:

```text
<data_root>/<publication-id>/...
```

It rejects absolute paths and `..`, and it creates parent directories automatically.

Format-specific helpers should generally come in save/load pairs when `pubify-pubs` owns the format handling.

## CLI

The installed command is `pubs`:

- `pubs list`
- `pubs init <publication-id>`
- `pubs <publication-id> check`
- `pubs <publication-id> shell`
- `pubs <publication-id> export [<figure-id> [<subfig-idx>]]`
- `pubs <publication-id> data [list]`
- `pubs <publication-id> data <loader-id> pin`
- `pubs <publication-id> figure [list|<figure-id> preview]`
- `pubs <publication-id> ignore <relative-path>`
- `pubs <publication-id> build [--export|--export-if-stale]`
- `pubs <publication-id> preview`
- `pubs <publication-id> push [--force]`
- `pubs <publication-id> pull [--force]`
- `pubs <publication-id> diff [list|<relative-path>]`

### Command Semantics

- `list`
  - lists available publication ids under the configured workspace publication root
- `init`
  - creates a new publication skeleton with package-owned starter files
- `check`
  - loads and validates the publication definition
- `shell`
  - opens a publication-scoped interactive session with prompt `<publication-id>> `
  - supports command history and standard line editing
  - reloads publication code and config when `figures.py`, `pub.yaml`, or publication-local helpers change
- `figure list`
  - lists discovered figures and their declared loader dependencies
- `figure <figure-id> preview`
  - opens the exported PDF for one figure from `tex/autofigures/`
  - uses the `preview.figure` backend from `pubify.conf`
  - opens all matching panel PDFs for multi-panel figures
- `export`
  - writes generated figure PDFs into `tex/autofigures/`
  - full export clears stale generated outputs first
  - targeted export does not clear unrelated generated outputs
- `data list`
  - reports one row per declared loader path with status `pinned` or `external`
- `data <loader-id> pin`
  - copies the loader's declared external input paths into pinned publication-local data under `data_root`
  - mechanically rewrites the targeted loader from `@external_data(...)` to `@data(...)` when that rewrite is safe
- `ignore <relative-path>`
  - records a mirror-sync exclusion in the publication config
- `build`
  - builds from the current publication-local TeX tree
  - `--export` runs a full export before building
  - `--export-if-stale` exports first only when `figures.py` appears newer than the generated outputs or the generated output directory is missing or empty
- `preview`
  - opens the built publication PDF derived from `main_tex`
  - uses the `preview.publication` backend from `pubify.conf`
- `diff`, `push`, `pull`
  - operate on the canonical publication-local TeX tree and mirror state using conservative sync rules

## Generated Figures And TeX Assets

`tex/autofigures/` is the framework-owned generated figure directory.

- generated figures from `figures.py` are exported there
- full export treats it as an authoritative snapshot and clears stale generated files first
- TeX should reference generated figures explicitly by path such as `autofigures/<name>.pdf`

Manual and static paper assets are ordinary publication-local TeX files. They are not part of the generated export surface and do not belong in the framework-owned `autofigures` directory.

## Mirror Sync Model

The local TeX tree is canonical.

Managed source files are the publication-local TeX sources under `tex/`, excluding:

- generated figures in `tex/autofigures/`
- build artifacts in `tex/build/`
- publication-local sync exclusions from `pub.yaml`

Generated figures are delivered one-way from local `tex/autofigures/` to mirror `autofigures/`. They are not part of the hash-managed source sync model.

Managed source-file statuses are:

- `unchanged`
- `local-only`
- `mirror-only`
- `local-changed`
- `mirror-changed`
- `in-sync`
- `conflicting`

Meaning:

- `local-only` and `mirror-only`
  - the file exists only on that side
- `local-changed` and `mirror-changed`
  - the file exists on both sides, but only one side changed relative to the synced baseline
- `in-sync`
  - both sides changed relative to the baseline but currently match each other
- `conflicting`
  - both sides changed and do not match

`push` and `pull` are conservative:

- unilateral non-conflicting changes copy in the requested direction
- `conflicting` blocks directional sync unless `--force` is used
- `--force` applies directionally; it does not make sync symmetric or destructive beyond the requested direction

## Development

Install the package in editable mode:

```bash
pip install -e .
```

Run the package tests:

```bash
pytest
```

Build the docs site:

```bash
mkdocs build
```

Documentation lives under `docs/` and is built with MkDocs.

## License

MIT
