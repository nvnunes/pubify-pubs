# pubify-pubs Docs

`pubify-pubs` is a local-first publication workflow package built around `pubify-mpl`.

It is meant for host workspaces that keep publication content, publication-local TeX sources, and pinned inputs under version control, while the package owns the generic workflow around:

- workspace discovery through `pubify.conf`
- publication discovery and validation
- figure export into publication-local `tex/autofigures/`
- generated stats into publication-local `tex/autostats.tex`
- generated tables into publication-local `tex/autotables.tex`
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
preview:
  publication: preview
  figure: preview
```

The package discovers that file by walking upward from the current working directory.

The workspace contract is:

- `publications_root` points at publication folders owned by the host workspace
- `data_root` points at pinned publication-local data owned by the host workspace
- package code lives independently from both

Keeping publication content and pinned data separate is intentional. It lets host workspaces keep publication source trees readable while still giving the workflow a stable place to store pinned inputs.

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
pubs my-paper check
pubs my-paper update
pubs my-paper build
```

That creates a minimal publication skeleton and installs package-owned support files into the publication-local TeX tree.

## Typical Workflow

1. Keep publication-local TeX sources under `papers/<publication-id>/tex/`.
2. Define loaders, figure functions, stats, and tables in `figures.py`.
3. Run `pubs <publication-id> check` to load and validate the publication definition.
4. Run `pubs <publication-id> update` to refresh generated figures, stats, and tables.
5. Run `pubs <publication-id> build` to compile the publication.
6. If a synced mirror is configured, use `diff`, `push`, or `pull` as needed.
7. When an external loader input should become publication-local and reproducible, pin it with `pubs <publication-id> data <loader-id> pin`.

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
from pubify_pubs.decorators import data, external_data, figure, table
from pubify_pubs.export import FigureExport, panel
```

`@figure` marks a callable as a logical publication figure. Exported figure functions may return:

- a Matplotlib `Figure`
- a Matplotlib `Axes`
- a sequence of figures or axes
- a `FigureExport` value for explicit multi-panel control

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

`FigureExport` also carries first-class caption sizing metadata for `pubify-mpl`:

- `layout`
  - optional explicit layout override
  - when omitted, `pubify-pubs` uses the publication default layout from `pub.yaml`
- `caption_lines`
  - estimated main-caption line count
- `subcaption_lines`
  - default estimated subcaption line count for panels

If one panel needs a different subcaption estimate, `panel(..., subcaption_lines=...)` overrides the figure-level default for that panel only.

When a plotting library creates text artists during figure construction, use `ctx.rc` so those artists are born under the publication construction-time font defaults:

```python
@figure
def custom_map(ctx):
    with ctx.rc:
        fig = build_custom_map()
    return fig
```

The intended styling flow is:

- build under `ctx.rc` when a plotting library reads Matplotlib defaults during construction
- let pubify run its full export-time setup plus generic post-construction normalization during export
- use `prepare_export(...)` only for figure-specific text or artists that still need special handling

For those remaining custom text cases, pass a `prepare_export` callback through `FigureExport(..., kwargs={...})` and use the resolved style payload:

```python
def prepare_export(fig_export, style):
    sky_ax = fig_export.axes[0]
    for text in iter_custom_tick_labels(sky_ax):
        text.set_fontfamily(style.font_family)
        text.set_fontsize(style.tick_labelsize_pt)

return FigureExport(fig, kwargs={"prepare_export": prepare_export})
```

One-argument callbacks still work, but the two-argument form is preferred for figure-specific styling adjustments after the generic pubify passes have already run.

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
- stats return either:
  - one value, which is coerced with `str(...)` and emits `\Stat<StatId>`
  - or a `dict[str, object]`, whose values are coerced with `str(...)` and emit `\Stat<StatId><Key>`
- console display is derived from the TeX-facing value with light cleanup for common TeX markup such as `$...$`, `\,`, and `\mathrm{...}`

`tex/autotables.tex` is the framework-owned generated tables file.

- `table update` rewrites it as one authoritative snapshot
- `table <table-id> update` still rewrites the full snapshot after computing the selected table
- TeX should include it explicitly, for example with `\input{autotables.tex}`
- single-body tables emit `\Table<Id>`
- multi-body tables emit `\Table<Id>{1}`, `\Table<Id>{2}`, ...
- `table check` and publication-wide `check` validate logical table width against direct manuscript uses inside supported environments such as `tabular`, `tabularx`, and `longtable`
- unsupported wrappers or unrecognized column-spec syntax fail explicitly rather than falling back to heuristics

Manual and static publication assets remain ordinary publication-local TeX files. They do not belong in `tex/autofigures/`.

## Mirror Sync Model

The local publication TeX tree is canonical.

Managed source files are publication-local TeX sources under `tex/`, excluding:

- generated figures in `tex/autofigures/`
- build artifacts in `tex/build/`
- publication-local sync exclusions from `pub.yaml`

Generated figures are delivered one-way from local `tex/autofigures/` to mirror `autofigures/`. `autostats.tex` and `autotables.tex` are also delivered one-way to the mirror. None of those generated outputs are part of the hash-managed source sync model.

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

- `pubs <publication-id> prepare`
- `pubs <publication-id> check`
- `pubs <publication-id> update`
- `pubs <publication-id> shell`
- `pubs <publication-id> figure [list|add <figure-id>|update|<figure-id> update|<figure-id> preview [<subfig-idx>]]`
- `pubs <publication-id> stat [list|add <stat-id>|update|<stat-id> update]`
- `pubs <publication-id> table [list|add <table-id>|update|check|<table-id> update|<table-id> check]`
- `pubs <publication-id> tables ...`
- `pubs <publication-id> data [list|add <data-id>]`
- `pubs <publication-id> data <loader-id> pin`
- `pubs <publication-id> ignore <relative-path>`
- `pubs <publication-id> build [--update|--skipupdate] [--clear]`
- `pubs <publication-id> preview`
- `pubs <publication-id> push [--force]`
- `pubs <publication-id> pull [--force]`
- `pubs <publication-id> diff [list|<relative-path>]`

`update` refreshes publication code/config state plus generated figures, stats, and tables. By default, `build` refreshes generated figures, stats, and tables before LaTeX build only when `figures.py` is newer than the generated outputs, `tex/autofigures/` is missing or empty, `tex/autostats.tex` is missing, or `tex/autotables.tex` is missing. `build --update` forces that refresh, and `build --skipupdate` skips it. In `pubs <publication-id> shell`, the first `build` after shell start or after `update` also forces one refresh unless `--skipupdate` is used.

`tables` is an alias for `table` in both the CLI and the publication shell.

The shell command opens a publication-scoped interactive session with command history and automatic pickup of changes to `figures.py`, `pub.yaml`, and publication-local helpers. Shell `update` forces a publication refresh and then regenerates figures, stats, and tables. Normal loader data is loaded on shell start and again when the publication is refreshed, then reused across shell commands. `nocache=True` loaders rerun once per command.

Preview behavior is workspace-configured:

- `pubs <publication-id> preview`
  - opens the built publication PDF derived from `main_tex`
  - uses `preview.publication`
- `pubs <publication-id> figure <figure-id> preview`
  - opens exported PDFs from `tex/autofigures/`
  - uses `preview.figure`

## Python API

The public Python API is intentionally small.

Primary entrypoints:

- `find_workspace_root(...)`
- `figure`, `stat`, `data`, `external_data`
- `publication_data_path(...)`
- `save_publication_data_npz(...)`
- `load_publication_data_npz(...)`
- `FigureExport`

## Generated Stats

`tex/autostats.tex` is the framework-owned generated stats file.

- `pubs <publication-id> stat update` rewrites it as one authoritative snapshot
- `pubs <publication-id> stat <stat-id> update` prints one stat block to the console while still rewriting the full snapshot
- TeX should include it explicitly, for example with `\input{autostats.tex}`
- stats return either:
  - one value, which is coerced with `str(...)` and emits `\Stat<StatId>`
  - or a `dict[str, object]`, whose values are coerced with `str(...)` and emit `\Stat<StatId><Key>`
- console display is derived from the TeX-facing value with light cleanup for common TeX markup such as `$...$`, `\,`, and `\mathrm{...}`
- In prose, use `{}` after a stat macro before following letters, for example `\StatFavorableAsterismCount{} targets`.
- stat ids stay `snake_case` in Python, but generated TeX macro names use CamelCase
  - `compute_favorable_asterism_count(...)` maps to `\StatFavorableAsterismCount`
  - figure files stay `snake_case`, for example `tex/autofigures/ews_asterism_coverage_map_1.pdf`

Generated stat macros use the forms:

- `\Stat<StatId>`
- `\Stat<StatId><Key>`

See the [API reference](api.md) for the docstring-driven reference pages.
