# pubify-pubs Docs

`pubify-pubs` is a local-first publication workflow package built around `pubify-mpl`.

It is meant for host workspaces that keep publication content, publication-local TeX sources, and pinned inputs under version control, while the package owns the generic workflow around:

- workspace discovery through `pubify.conf`
- publication discovery and validation
- figure export into publication-local `tex/autofigures/`
- generated stats into publication-local `tex/autostats.tex`
- generated tables into publication-local `tex/autotables.tex`
- LaTeX builds against the publication-local `tex/` tree
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
preview:
  publication: preview
  figure: preview
```

The package discovers that file by walking upward from the current working directory.

The workspace contract is:

- `publications_root` points at publication folders owned by the host workspace
- `data_root` points at pinned publication-local data owned by the host workspace
- package code lives independently from both

The optional `preview` section configures how PDFs are opened:

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

If `preview` is omitted, both commands default to `preview`.

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
  - defines manuscript stats with `@stat`
  - defines manuscript tables with `@table`
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
pubs my-paper update
pubs my-paper build
```

That creates a minimal publication skeleton and installs package-owned support files into the publication-local TeX tree.

## Typical Workflow

1. Keep publication-local TeX sources under `papers/<publication-id>/tex/`.
2. Define loaders, figure functions, stats, and tables in `figures.py`.
3. Run `pubs <publication-id> update` to refresh package-owned TeX support files, validate the publication definition, and regenerate figures, stats, and tables.
4. Run `pubs <publication-id> build` to validate and compile the publication.
5. Use `pubs <publication-id> preview` or `pubs <publication-id> figure <figure-id> preview` while iterating.
6. Use the advanced workflows only when needed:
   - data pinning: [Pinning](pinning.md)

To scaffold starter entrypoints directly into `figures.py`:

- `pubs <publication-id> data add <data-id>`
- `pubs <publication-id> figure add <figure-id>`
- `pubs <publication-id> stat add <stat-id>`
- `pubs <publication-id> table add <table-id>`

## Figures, Tables, And Loaders

Prefer `@data(...)` for pinned publication-local inputs under the configured workspace `data_root`. Use `@external_data(...)` only for explicit external roots declared in `pub.yaml`.

Both data decorators require relative paths. They reject absolute paths and path traversal.

Host publications import from the extracted package namespace directly:

```python
from pubify_pubs.data import (
    load_publication_data_npz,
    publication_data_path,
    save_publication_data_npz,
)
from pubify_pubs import TableResult
from pubify_pubs.decorators import data, external_data, figure, stat, table
from pubify_pubs.export import FigureExport, panel
```

`@figure` marks a callable as a logical publication figure. Exported figure functions may return:

- a Matplotlib `Figure`
- a Matplotlib `Axes`
- a sequence of figures or axes
- a `FigureExport` value for explicit multi-panel control

`FigureExport` accepts a single Matplotlib `Figure` or `Axes`, a list/tuple of them, one `panel(...)`, or a list/tuple of `panel(...)` values.

When a plotting library creates text artists during figure construction, use `ctx.rc` so those artists are born under the publication construction-time font defaults:

```python
@figure
def custom_map(ctx):
    with ctx.rc:
        fig = build_custom_map()
    return fig
```

`@table` marks a callable as a logical publication table. Table functions return `TableResult(...)`, which owns logical table data and simple rendering while LaTeX keeps ownership of headers, captions, labels, rules, and layout.

```python
@table
def tabulate_summary(ctx):
    return TableResult(
        [
            ["Metric", "Value"],
            ["Count", 3],
            ["Mean", 2.00],
        ],
        formats=["{}", "{:.2f}"],
    )
```

`TableResult` accepts one 2D body or a sequence of 2D bodies. Nested lists, tuples, and NumPy arrays are supported when they are unambiguously 2D or 3D.

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

## Generated Figures, Stats, Tables, And TeX Assets

`tex/autofigures/` is the framework-owned generated figure directory.

- full `figure update` treats it as an authoritative snapshot and clears stale generated files first
- targeted `figure <figure-id> update` stays incremental
- TeX should reference generated figures explicitly by path such as `autofigures/<name>.pdf`

`tex/autostats.tex` is the framework-owned generated stats file.

- `stat update` rewrites it as one authoritative snapshot
- TeX should include it explicitly, for example with `\input{autostats.tex}`
- stats return either one value or a `dict[str, object]`
- generated stat macros are named `\Stat<StatId>` and `\Stat<StatId><Key>`

`tex/autotables.tex` is the framework-owned generated tables file.

- `table update` rewrites it as one authoritative snapshot
- `table <table-id> update` still rewrites the full snapshot after computing the selected table
- TeX should include it explicitly, for example with `\input{autotables.tex}`
- single-body tables emit `\Table<Id>`
- multi-body tables emit `\Table<Id>{1}`, `\Table<Id>{2}`, ...
- `update` and `build` validate logical table width against direct manuscript uses inside supported environments such as `tabular`, `tabularx`, and `longtable`

Manual and static publication assets remain ordinary publication-local TeX files. They do not belong in `tex/autofigures/`.

## CLI

The installed command is `pubs`.

Top-level commands:

- `pubs list`
- `pubs init <publication-id>`

Publication commands:

- `pubs <publication-id> shell`
- `pubs <publication-id> data [list|add <data-id>]`
- `pubs <publication-id> figure [list|add <figure-id>|update|<figure-id> update|<figure-id> preview [<subfig-idx>]|<figure-id> latex [subcaption]]`
- `pubs <publication-id> stat [list|add <stat-id>|update|<stat-id> update|<stat-id> latex]`
- `pubs <publication-id> table [list|add <table-id>|update|<table-id> update|<table-id> latex]`
- `pubs <publication-id> update`
- `pubs <publication-id> build [--clear]`
- `pubs <publication-id> preview`

Advanced workflows:

- `pubs <publication-id> data <loader-id> pin`

`update` refreshes package-owned TeX support files, validates the publication definition, and regenerates figures, stats, and tables. `build` refreshes package-owned TeX support files, validates the publication definition, and then compiles the current TeX tree; it does not regenerate figures, stats, or tables, so run `update` first when generated outputs need refreshing.

`tables` is an alias for `table` in both the CLI and the publication shell.

The `latex` commands are read-only convenience helpers. They never edit manuscript files, and they print one blank line above and below the emitted snippet to make terminal selection easier. `tex` is accepted as an alias for `latex`.

See the dedicated pages for the deferred workflows:

- [Pinning](pinning.md)

## Python API

The public Python API is intentionally small.

Primary entrypoints:

- `find_workspace_root(...)`
- `figure`, `stat`, `data`, `external_data`
- `publication_data_path(...)`
- `save_publication_data_npz(...)`
- `load_publication_data_npz(...)`
- `FigureExport`
- `TableResult`

See the [API reference](api.md) for the docstring-driven reference pages.
