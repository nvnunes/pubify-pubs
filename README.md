# pubify-pubs

`pubify-pubs` is a local-first publication workflow package built around `pubify-mpl`.

It is meant for host workspaces that keep publications, publication-local TeX sources, and pinned inputs under version control, while the package owns the workflow around publication discovery, figure export, LaTeX builds, publication bootstrapping, and a small set of optional advanced workflows such as data pinning.

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
- `figures.py` declares loaders, figures, stats, and tables
- generated figures are exported into `tex/autofigures/`
- generated stats are written into `tex/autostats.tex`
- generated tables are written into `tex/autotables.tex`
- LaTeX builds run against the publication-local `tex/` tree

The local publication tree is canonical.

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
pubs my-paper update
pubs my-paper build
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
- stats decorated with `@stat`
- tables decorated with `@table`

## Typical Workflow

1. Keep publication-local TeX sources under `papers/<publication-id>/tex/`.
2. Define loaders, figure functions, stats, and tables in `figures.py`.
3. Run `pubs <publication-id> update` to refresh package-owned TeX support files, validate the publication definition, and regenerate figures, stats, and tables.
4. Run `pubs <publication-id> build` to validate and compile the publication.
5. Use `pubs <publication-id> preview` or `pubs <publication-id> figure <figure-id> preview` while iterating.
6. Use the optional advanced workflows only when needed:
   - data pinning: see [`docs/pinning.md`](docs/pinning.md)

To scaffold starter entrypoints directly into `figures.py`:

- `pubs <publication-id> data add <data-id>`
- `pubs <publication-id> figure add <figure-id>`
- `pubs <publication-id> stat add <stat-id>`
- `pubs <publication-id> table add <table-id>`

## Figures, Tables, And Loaders

Prefer `@data(...)` for pinned publication-local inputs under the configured `data_root`. Use `@external_data(...)` only for explicit external roots declared in `pub.yaml`.

Host publications import from the extracted package namespace directly:

```python
from pubify_pubs.data import load_publication_data_npz, publication_data_path, save_publication_data_npz
from pubify_pubs import TableResult
from pubify_pubs.decorators import data, external_data, figure, stat, table
from pubify_pubs.export import FigureExport, panel
```

`@data(...)` and `@external_data(...)` both require relative paths. They reject absolute paths and path traversal.

`@figure` marks a callable as a logical publication figure. Exported figure functions typically return `FigureExport` values built from one or more panels.

```python
return FigureExport(fig, layout="one")
return FigureExport([fig1, fig2], layout="two")
```

Use `panel(...)` only when one panel needs extra pubify export metadata beyond the figure or axes itself, such as `subcaption_lines` or per-panel export overrides.

When a plotting library creates text artists during figure construction, build the figure under `ctx.rc` so those artists inherit publication font defaults at creation time:

```python
@figure
def custom_map(ctx):
    with ctx.rc:
        fig = build_custom_map()
    return fig
```

For figure-specific cleanup that pubify still cannot discover generically, pass `prepare_export(...)` through `FigureExport(..., kwargs={...})`.

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

Column rendering is intentionally small:

- `formats[col]`
  - `None`, `""`, or `"{}"` means `str(value)` then LaTeX-escape
  - ordinary format strings like `"{:.2f}"` format then escape
  - `"tex"` means the value itself is already TeX and is inserted raw
- `tex_wrappers[col]`
  - wrap the formatted value into raw TeX using one `@` placeholder
- `multicolumns`
  - enables compact horizontal merging without changing logical width

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

## Generated Figures, Stats, Tables, And TeX Assets

`tex/autofigures/` is the framework-owned generated figure directory.

- generated figures from `figures.py` are exported there
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

Manual and static paper assets are ordinary publication-local TeX files. They are not part of the generated export surface and do not belong in `tex/autofigures/`.

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

Optional advanced workflows:

- `pubs <publication-id> data <loader-id> pin`

`update` refreshes package-owned TeX support files, validates the publication definition, and regenerates figures, stats, and tables. `build` refreshes package-owned TeX support files, validates the publication definition, and then compiles the current TeX tree; it does not regenerate figures, stats, or tables, so run `update` first when generated outputs need refreshing.

`tables` is an alias for `table` in both the CLI and the publication shell.

The `latex` commands are read-only convenience helpers. They never edit manuscript files, and they print one blank line above and below the emitted snippet to make terminal selection easier. `tex` is accepted as an alias for `latex`.

See the dedicated docs for the deferred workflows:

- data pinning: [`docs/pinning.md`](docs/pinning.md)

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
mkdocs build --strict
```

## Development Approach

Keep publication-specific science code in host publications, not in this package.

## License

MIT
