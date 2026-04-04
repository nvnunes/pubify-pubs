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
- `figures.py` declares loaders, figures, stats, and tables
- generated figures are exported into `tex/autofigures/`
- generated stats are written into `tex/autostats.tex`
- generated tables are written into `tex/autotables.tex`
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
6. If you use a synced mirror such as a locally mounted Overleaf tree, run `diff`, `push`, or `pull` as needed.
7. When an external loader input should become publication-local and reproducible, pin it with `pubs <publication-id> data <loader-id> pin`.

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
from pubify_pubs.decorators import data, external_data, figure, table
from pubify_pubs.export import FigureExport, panel
```

`@data(...)` and `@external_data(...)` both require relative paths. They reject absolute paths and path traversal.

`@figure` marks a callable as a logical publication figure. Exported figure functions typically return `FigureExport` values built from one or more panels.

`FigureExport` accepts a single Matplotlib `Figure` or `Axes`, a list/tuple of them, one `panel(...)`, or a list/tuple of `panel(...)` values. Prefer passing raw figures or axes directly:

```python
return FigureExport(fig, layout="one")
return FigureExport([fig1, fig2], layout="two")
```

Use `panel(...)` only when one panel needs extra pubify export metadata beyond the figure or axes itself, such as `subcaption_lines` or per-panel export overrides:

```python
return FigureExport(
    [panel(fig1), panel(fig2, subcaption_lines=2, hide_cbar=True)],
    layout="two",
)
```

`FigureExport` also exposes first-class caption sizing fields for `pubify-mpl`:

- `layout`
  - optional explicit layout override
  - when omitted, `pubify-pubs` uses the publication default layout from `pub.yaml`
- `caption_lines`
  - estimated line count for the main figure caption
- `subcaption_lines`
  - default estimated line count for panel subcaptions

If panels differ materially, `panel(..., subcaption_lines=...)` can override the figure-level subcaption count per panel.

When a plotting library creates text artists during figure construction, build the figure under `ctx.rc` so those artists inherit publication font defaults at creation time:

```python
@figure
def custom_map(ctx):
    with ctx.rc:
        fig = build_custom_map()
    return fig
```

Publication styling now has three stages:

- build under `ctx.rc` when construction-time rc matters
- let pubify apply its full export-time setup plus normal generic cleanup and normalization afterward
- use `prepare_export(...)` only for figure-specific artists that pubify still cannot discover generically

For those remaining figure-specific cases, pass a `prepare_export` callback through `FigureExport(..., kwargs={...})`:

```python
def build_skymap():
    fig, ax = make_skymap()

    def prepare_export(fig_export, style):
        sky_ax = fig_export.axes[0]
        for text in iter_custom_tick_labels(sky_ax):
            text.set_fontfamily(style.font_family)
            text.set_fontsize(style.tick_labelsize_pt)

    return FigureExport(fig, kwargs={"prepare_export": prepare_export})
```

`prepare_export(fig_export)` still works. The preferred modern form is `prepare_export(fig_export, style)`, where `style` carries the resolved pubify styling values for text, lines, ticks, and spines. Treat it as the final figure-specific adjustment step, not the primary way to opt into publication typography.

`@table` marks a callable as a logical publication table. Table functions return `TableResult(...)`, which owns logical table data and simple rendering rules while LaTeX keeps ownership of headers, captions, labels, rules, and layout.

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

Column rendering is intentionally small:

- `formats[col]`
  - `None`, `""`, or `"{}"` means `str(value)` then LaTeX-escape
  - ordinary format strings like `"{:.2f}"` format then escape
  - `"tex"` means the value itself is already TeX and is inserted raw
- `tex_wrappers[col]`
  - wrap the formatted value into raw TeX using one `@` placeholder

```python
return TableResult(
    [["Offset", 1.372]],
    formats=["{}", "{:.2f}"],
    tex_wrappers=[None, r"@\,\mathrm{mas}"],
)
```

`multicolumns` enables compact horizontal merging without changing logical width:

```python
return TableResult(
    [
        ["Primary", "Primary", "Primary"],
        [None, None, None],
        ["Mean", 1.2, 0.4],
    ],
    multicolumns=[
        [0, 2],
        [0, 2, "n/a"],
    ],
)
```

Each multicolumn spec is `[start, end]`, `[start, end, missing_display]`, or `[start, end, missing_display, missing_format]`. Duplicate-value multicolumns require identical `formats` and `tex_wrappers` across the span.

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
- `pubs <publication-id> shell`
- `pubs <publication-id> data [list|add <data-id>]`
- `pubs <publication-id> data <loader-id> pin`
- `pubs <publication-id> figure [list|add <figure-id>|update|<figure-id> update|<figure-id> preview [<subfig-idx>]|<figure-id> latex [subcaption]]`
- `pubs <publication-id> stat [list|add <stat-id>|update|<stat-id> update|<stat-id> latex]`
- `pubs <publication-id> table [list|add <table-id>|update|<table-id> update|<table-id> latex]`
- `pubs <publication-id> update`
- `pubs <publication-id> build [--clear]`
- `pubs <publication-id> preview`
- `pubs <publication-id> tables ...`
- `pubs <publication-id> version [list|create [note]|diff <version-id> [<version-id>]]`
- `pubs <publication-id> ignore <relative-path>`
- `pubs <publication-id> push [--force]`
- `pubs <publication-id> pull [--force]`
- `pubs <publication-id> diff [list|<relative-path>]`

### Command Semantics

- `list`
  - lists available publication ids under the configured workspace publication root
- `init`
  - creates a new publication skeleton with package-owned starter files
- `update`
  - refreshes package-owned TeX support files
  - validates the publication definition
  - regenerates all figures, stats, and tables
- `shell`
  - opens a publication-scoped interactive session with prompt `<publication-id>> `
  - supports command history and standard line editing
  - reloads publication code and config when `figures.py`, `pub.yaml`, or publication-local helpers change
  - eagerly loads normal loader data on shell start and again when the publication is refreshed
  - reuses normal loader data across shell commands; `nocache=True` loaders rerun once per command
- `figure list`
  - lists discovered figures and their declared loader dependencies
- `figure add <figure-id>`
  - appends a starter `@figure` scaffold to `figures.py`
- `figure update`
  - regenerates all figures into `tex/autofigures/`
  - clears stale generated figure outputs first
- `figure <figure-id> update`
  - regenerates one figure into `tex/autofigures/`
  - does not clear unrelated generated figure outputs
- `figure <figure-id> latex`
  - prints a paste-ready `\figfloat` scaffold with caption and `fig:<figure-id>` label
  - `subcaption` adds per-panel `\fig{...}[...][...]` entries for multi-panel figures only
  - `tex` is accepted as an alias for `latex`
- `stat list`
  - lists discovered stats from `figures.py`
- `stat add <stat-id>`
  - appends a starter `@stat` scaffold to `figures.py`
- `stat update`
  - computes all stats, rewrites `tex/autostats.tex`, and prints the emitted macro values
- `stat <stat-id> update`
  - prints one selected stat block to the console
  - still rewrites the full `tex/autostats.tex` snapshot
- `stat <stat-id> latex`
  - prints the generated `\Stat...{}` macro names for copy/paste into LaTeX
  - `tex` is accepted as an alias for `latex`
- `table list`
  - lists discovered tables from `figures.py`
- `table add <table-id>`
  - appends a starter `@table` scaffold to `figures.py`
- `table update`
  - computes all tables and rewrites `tex/autotables.tex`
- `table <table-id> update`
  - updates one selected table while still rewriting the full `tex/autotables.tex` snapshot
- `table <table-id> latex`
  - prints a paste-ready `table`/`tabular` scaffold with generic `Column N` headers
  - multi-body tables are grouped with full-width `\multicolumn` separator rows
  - `tex` is accepted as an alias for `latex`
- `version list`
  - lists stored TeX snapshots under `tex/versions/`
- `version create [note]`
  - snapshots the current non-build TeX tree into the next `tex/versions/vN/`
  - stores the optional note plus timestamp in `tex/versions/metadata.yaml`
- `version diff <version-id>`
  - compares one stored version to the current live `tex/` tree
  - runs `latexdiff`, builds the redline PDF, and writes it into `tex/build/`
- `version diff <version-id> <version-id>`
  - compares two stored versions regardless of argument order
  - normalizes internally to older -> newer and writes `<main-stem>-diff-v<older>-v<newer>.pdf` into `tex/build/`
- `figure <figure-id> preview`
  - opens the exported PDF for one figure from `tex/autofigures/`
  - uses the `preview.figure` backend from `pubify.conf`
  - opens all matching panel PDFs for multi-panel figures
- `data list`
  - reports one row per declared loader path with status `pinned` or `external`
- `data add <data-id>`
  - inserts a starter `@data(...)` loader scaffold into `figures.py`
- `data <loader-id> pin`
  - copies the loader's declared external input paths into pinned publication-local data under `data_root`
  - mechanically rewrites the targeted loader from `@external_data(...)` to `@data(...)` when that rewrite is safe
- `ignore <relative-path>`
  - records a mirror-sync exclusion in the publication config
- `build`
  - builds from the current publication-local TeX tree
  - refreshes package-owned TeX support files and validates the publication definition before building
  - does not regenerate figures, stats, or tables; run `update` first when generated outputs need refreshing
- `preview`
  - opens the built publication PDF derived from `main_tex`
  - uses the `preview.publication` backend from `pubify.conf`
- `diff`, `push`, `pull`
  - operate on the canonical publication-local TeX tree and mirror state using conservative sync rules

`tables` is an alias for `table` in both the CLI and the publication shell.

The `latex` commands are read-only convenience helpers. They never edit manuscript files, and they print one blank line above and below the emitted snippet to make terminal selection easier.
When possible, they also prepend a missing manuscript prelude line from the current `main.tex`: `figure ... latex` adds `\usepackage{pubify}` if needed, `stat ... latex` adds `\input{autostats.tex}` if needed, and `table ... latex` adds `\input{autotables.tex}` if needed.

The `version diff` workflow prepares a temporary TeX tree, overlays newer assets on older assets, writes the diffed `main_tex` entrypoint into that tree, runs `latexmk` there, and copies the resulting redline PDF back into the live `tex/build/` directory.

## Generated Figures, Stats, Tables, And TeX Assets

`tex/autofigures/` is the framework-owned generated figure directory.

- generated figures from `figures.py` are exported there
- full `figure update` treats it as an authoritative snapshot and clears stale generated files first
- TeX should reference generated figures explicitly by path such as `autofigures/<name>.pdf`

`tex/autostats.tex` is the framework-owned generated stats file.

- `stat update` rewrites it as one authoritative snapshot
- TeX should include it explicitly, for example with `\input{autostats.tex}`
- stats return either:
  - one value, which is coerced with `str(...)` and emits `\Stat<StatId>`
  - or a `dict[str, object]`, whose values are coerced with `str(...)` and emit `\Stat<StatId><Key>`
- generated stat macros are named `\Stat<StatId>` and `\Stat<StatId><Key>`
- console display is derived from the TeX-facing value with light cleanup for common TeX markup such as `$...$`, `\,`, and `\mathrm{...}`
- In prose, use `{}` after a stat macro before following letters, for example `\StatFavorableAsterismCount{} targets`.
- stat ids stay `snake_case` in Python, but generated TeX macro names use CamelCase

`tex/autotables.tex` is the framework-owned generated tables file.

- `table update` rewrites it as one authoritative snapshot
- `table <table-id> update` still rewrites the full snapshot after computing the selected table
- TeX should include it explicitly, for example with `\input{autotables.tex}`
- single-body tables emit `\Table<Id>`
- multi-body tables emit `\Table<Id>{1}`, `\Table<Id>{2}`, ...
- the publication validation performed by `update` and `build` validates logical table width against direct manuscript uses inside supported environments such as `tabular`, `tabularx`, and `longtable`
- unsupported wrappers or unrecognized column-spec syntax fail explicitly rather than falling back to heuristics
  - `compute_favorable_asterism_count(...)` maps to `\StatFavorableAsterismCount`
  - figure files stay `snake_case`, for example `tex/autofigures/ews_asterism_coverage_map_1.pdf`

Example stat authoring in `figures.py`:

```python
from pubify_pubs.decorators import stat

@stat
def compute_detection_summary(ctx, detections):
    found = int(detections["found"])
    total = int(detections["total"])
    fraction = found / total

    return {
        "Count": found,
        "Total": total,
        "Fraction": f"{fraction:.3f}",
    }
```

Manual and static paper assets are ordinary publication-local TeX files. They are not part of the generated export surface and do not belong in the framework-owned `autofigures` directory.

## Mirror Sync Model

The local TeX tree is canonical.

Managed source files are the publication-local TeX sources under `tex/`, excluding:

- generated figures in `tex/autofigures/`
- generated stats in `tex/autostats.tex`
- build artifacts in `tex/build/`
- publication-local sync exclusions from `pub.yaml`

Generated figures are delivered one-way from local `tex/autofigures/` to mirror `autofigures/`. `autostats.tex` and `autotables.tex` are also delivered one-way to the mirror. None of those generated outputs are part of the hash-managed source sync model.

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

## Development Approach

The implementation is intentionally pragmatic. Priority was given to producing a useful, validated tool rather than to maximizing internal elegance or generality. Parts of the implementation were developed with AI-assisted workflows. Development effort was focused on documented behavior, intended performance, and validation rather than on highly refined internal structure.

## License

MIT
