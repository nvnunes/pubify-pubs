# pubify-pubs

`pubify-pubs` is a local-first publication workflow package built around `pubify-tex`.

It is the LaTeX-oriented downstream package for the TeX-agnostic `pubify-data`
runtime. It is meant for host workspaces that keep publications,
publication-local TeX sources, and pinned inputs, while `pubify-pubs` owns the
LaTeX, `pubify-tex`, preview, build, sync, and publication-bootstrapping
workflow.

This package does not own your publications. A host workspace does.

## Project Docs

- [Documentation home](https://nvnunes.github.io/pubify-pubs/)
- [Architecture](https://nvnunes.github.io/pubify-pubs/architecture/)
- [Development setup](https://nvnunes.github.io/pubify-pubs/development/)
- [Testing and validation](https://nvnunes.github.io/pubify-pubs/testing/)
- [API reference](https://nvnunes.github.io/pubify-pubs/api/)
- [Contributing](https://github.com/nvnunes/pubify-pubs/blob/main/CONTRIBUTING.md)
- [Changelog](https://github.com/nvnunes/pubify-pubs/blob/main/CHANGELOG.md)

## Requirements

- Python 3.10+
- `pubify-tex`
- a working LaTeX installation for `pubs <publication-id> build`

The build command runs `latexmk` against the publication-local TeX tree. If exported figures use LaTeX text rendering through `pubify-tex`, LaTeX must also be available during Python-side figure export.

## How It Works

`pubify-pubs` treats a configured host workspace as the source of truth and
uses `pubify-data` for reusable decorator discovery, loader execution, neutral
runtime results, and neutral list/update command dispatch.

- `pubify.yaml` contains a `pubify-pubs` section that defines where publications live
- each publication lives under `papers/<publication-id>/`
- `figures.py` declares loaders, figures, stats, and tables
- pinned publication data lives under `papers/<publication-id>/data/`
- generated figures, stats, and tables are stored under `data/tex-artifacts/`
- the TeX tree exposes those generated artifacts through local symlinks such as `tex/autofigures/`
- LaTeX builds run against the publication-local `tex/` tree

The local publication tree is canonical.

## Quick Start

Initialize a workspace:

```bash
pubs init
```

That writes `pubify.yaml` like:

```yaml
pubify-pubs:
  publications_root: papers
  preview:
    publication: preview
    figure: preview
```

Then initialize a new publication:

```bash
pubs init my-paper
```

That creates a publication skeleton like:

```text
papers/my-paper/
  data/
    tex-artifacts/
      autofigures/
      autostats.tex
      autotables.tex
  figures.py
  pub.yaml
  tex/
    main.tex
    autofigures -> ../data/tex-artifacts/autofigures
    autostats.tex -> ../data/tex-artifacts/autostats.tex
    autotables.tex -> ../data/tex-artifacts/autotables.tex
    build/
```

Then iterate with:

```bash
pubs my-paper update
pubs my-paper build
```

`pubs init` also creates a minimal shared `AGENTS.md` under the configured
`publications_root`, which is `papers/AGENTS.md` by default.

## Workspace Model

A host workspace is rooted by `pubify.yaml`. The package discovers that file by walking upward from the current working directory.

`pubify-pubs.publications_root` contains publication directories. Pinned publication-local data resolves under:

```text
papers/<publication-id>/data/...
```

If a host wants to keep the physical data elsewhere, make the publication-local
`data/` path a filesystem redirect, such as a symlink:

```text
papers/<publication-id>/data -> ../../output/papers/<publication-id>
```

`pubify-pubs` treats that redirected `data/` path as the publication data root;
there is no workspace-level `data_root` setting.

`pubify.yaml` can also configure preview backends independently for publication PDFs and exported figure PDFs:

```yaml
pubify-pubs:
  publications_root: papers
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
  data/
    tex-artifacts/
  tex/
    main.tex
    autofigures -> ../data/tex-artifacts/autofigures
    autostats.tex -> ../data/tex-artifacts/autostats.tex
    autotables.tex -> ../data/tex-artifacts/autotables.tex
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

The publication-local `data/tex-artifacts/` tree is canonical for generated figures, stats, and tables. The publication-local `tex/` tree exposes a symlink view for LaTeX convenience, and `tex/build/` is the local build output directory.

## Typical Workflow

1. Keep publication-local TeX sources under `papers/<publication-id>/tex/`.
2. Define loaders, figure functions, stats, and tables in `figures.py`.
3. Run `pubs <publication-id> update` to refresh package-owned TeX support files, validate the publication definition, and regenerate figures, stats, and tables.
4. Run `pubs <publication-id> build` to validate and compile the publication.
5. Use `pubs <publication-id> preview` or `pubs <publication-id> figure <figure-id> preview` while iterating.

To scaffold starter entrypoints directly into `figures.py`:

- `pubs <publication-id> data add <data-id>`
- `pubs <publication-id> figure add <figure-id>`
- `pubs <publication-id> stat add <stat-id>`
- `pubs <publication-id> table add <table-id>`

## Figures, Tables, And Loaders

Prefer `@data(...)` for pinned publication-local inputs under `papers/<publication-id>/data/`. Use `@external_data(...)` only for explicit external roots declared in `pub.yaml`.

Both data decorators require relative paths. They reject absolute paths and path traversal.

Host publications import decorators from the upstream `pubify_data` namespace and LaTeX/export helpers from `pubify_pubs`:

```python
from pubify_pubs import FigureResult, StatResult, TableResult
from pubify_pubs.data import (
    load_publication_data_npz,
    publication_data_path,
    save_publication_data_npz,
)
from pubify_data import data, external_data, figure, stat, table
from pubify_pubs.export import panel
```

`@figure` marks a callable as a logical publication figure. Exported figure functions may return:

- a Matplotlib `Figure`
- a Matplotlib `Axes`
- a sequence of figures or axes
- a `FigureResult` value for explicit multi-panel control

`FigureResult` accepts a single Matplotlib `Figure` or `Axes`, a list or tuple of them, one `panel(...)`, or a list or tuple of `panel(...)` values.

Exported figure functions commonly return `FigureResult` values built from one or more panels:

```python
return FigureResult(fig, layout="onewide")
return FigureResult([fig1, fig2], layout="twowide")
```

Use `panel(...)` only when one panel needs extra pubify export metadata beyond the figure or axes itself, such as `subcaption_lines` or per-panel export overrides.

When a plotting library creates text artists during figure construction, use `ctx.rc` so those artists are born under the publication construction-time font defaults:

```python
@figure
def custom_map(ctx):
    with ctx.rc:
        fig = build_custom_map()
    return fig
```

For figure-specific cleanup that pubify still cannot discover generically, pass `prepare_export(...)` through `FigureResult(..., kwargs={...})`.

### Reusing Outputs From Another Pubify Publication

A publication can reuse code from another pubify publication by declaring a
source in `pub.yaml`:

```yaml
sources:
  talk: slides/test
```

The source root uses the conventional pubify layout with `figures.py` and
`data/`. Source outputs are available inside local wrapper functions through
`ctx.source(...)`:

```python
from pubify_data import figure, stat
from pubify_pubs import FigureResult, StatResult


@figure
def plot_reused_slide_figure(ctx):
    panel = ctx.source("talk").figure("summary_plot").panel(1)
    return FigureResult(panel, layout="onewide", caption_lines=2)


@stat
def compute_reused_slide_count(ctx):
    source_stat = ctx.source("talk").stat("summary_count")
    return StatResult(source_stat.values[0].value)
```

Manuscripts should reference the local wrapper IDs, not source-qualified IDs.
This keeps TeX stable when the source publication is reorganized.

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

These helpers resolve data under:

```text
papers/<publication-id>/data/...
```

`publication_data_path(...)` resolves paths under that root. It rejects absolute paths and `..`, and it creates parent directories automatically.

Format-specific helpers should generally come in save/load pairs when `pubify-pubs` owns the format handling.

## Generated Figures, Stats, Tables, And TeX Assets

`data/tex-artifacts/autofigures/` is the canonical framework-owned generated figure directory. `tex/autofigures` is a symlink view for LaTeX.

- generated figures from `figures.py` are exported to the canonical directory
- full `figure update` treats the canonical directory as an authoritative snapshot and clears stale generated files first
- targeted `figure <figure-id> update` stays incremental
- TeX should reference generated figures explicitly by path such as `autofigures/<name>.pdf`

`data/tex-artifacts/autostats.tex` is the canonical framework-owned generated stats file. `tex/autostats.tex` is a symlink view for LaTeX.

- `stat update` rewrites it as one authoritative snapshot
- TeX should include it explicitly, for example with `\input{autostats.tex}`
- stats return either one value or a `dict[str, object]`
- generated stat macros are named `\Stat<StatId>` and `\Stat<StatId><Key>`

`data/tex-artifacts/autotables.tex` is the canonical framework-owned generated tables file. `tex/autotables.tex` is a symlink view for LaTeX.

- `table update` rewrites it as one authoritative snapshot
- `table <table-id> update` still rewrites the full snapshot after computing the selected table
- TeX should include it explicitly, for example with `\input{autotables.tex}`
- single-body tables emit `\Table<Id>`
- multi-body tables emit `\Table<Id>{1}`, `\Table<Id>{2}`, ...
- `update` and `build` validate logical table width against direct manuscript uses inside supported environments such as `tabular`, `tabularx`, and `longtable`
- `build` validates and compiles the current TeX tree, but does not regenerate figures, stats, or tables

Manual and static publication assets remain ordinary publication-local TeX files. They do not belong in `data/tex-artifacts/` or the `tex/autofigures` symlink view.

## CLI Overview

The installed command is `pubs`.

Top-level commands:

- `pubs list`
- `pubs init`
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

`update` refreshes package-owned TeX support files, validates the publication definition, and regenerates figures, stats, and tables. `build` validates and compiles the current publication-local TeX tree; it does not regenerate figures, stats, or tables, so run `update` first when generated outputs need refreshing.

`tables` is an alias for `table` in both the CLI and the publication shell.

The `latex` commands are read-only convenience helpers. They never edit manuscript files, and they print one blank line above and below the emitted snippet to make terminal selection easier. `tex` is accepted as an alias for `latex`.

## Python API Overview

The public Python API is intentionally small. Host publications import reusable
authoring decorators from `pubify_data` and LaTeX/export helpers from
`pubify_pubs`:

```python
from pubify_pubs import FigureResult, StatResult, TableResult
from pubify_pubs.data import (
    load_publication_data_npz,
    publication_data_path,
    save_publication_data_npz,
)
from pubify_data import data, external_data, figure, stat, table
from pubify_pubs.export import panel
from pubify_pubs.discovery import find_workspace_root
```

Use the [API reference](https://nvnunes.github.io/pubify-pubs/api/) for the docstring-driven reference pages.

## Development

Use the repo-local `./.conda` environment by default for Python commands, test runs, and docs builds. The durable contributor workflow lives in the [development setup](https://nvnunes.github.io/pubify-pubs/development/) and [testing](https://nvnunes.github.io/pubify-pubs/testing/) docs.

## License

MIT
