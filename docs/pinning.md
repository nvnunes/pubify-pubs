# Pinning

`pubify-pubs` can mechanically convert an external loader input into publication-local pinned data under the workspace `data_root`.

This is useful when a previously external input should become reproducible and publication-owned.

## Commands

- `pubs <publication-id> data <loader-id> pin`

## Data Model

Pinned publication data lives under:

```text
<data_root>/<publication-id>/...
```

Use `@data(...)` when an input should live there. Use `@external_data(...)` only for explicit external roots declared in `pub.yaml`.

The package also provides small helpers for publication-owned formats:

- `publication_data_path(...)`
- `save_publication_data_npz(...)`
- `load_publication_data_npz(...)`

## What `data <loader-id> pin` Does

The pinning command:

1. copies the loader's declared external input paths into publication-local pinned data under `data_root`
2. rewrites the targeted loader from `@external_data(...)` to `@data(...)` when that rewrite is mechanically safe

It is intentionally narrow. It operates on the selected loader and the loader paths it declares.

## Typical Use

Suppose a publication starts with an external input:

```python
@external_data("catalogs/source_table.npz")
def load_source_table(ctx):
    ...
```

After:

```bash
pubs my-paper data source_table pin
```

the input is copied into the publication-owned data root, and when safe the loader is rewritten to the pinned `@data(...)` form.

## Why Keep Pinning Separate

Figures, stats, and tables generate manuscript-facing artifacts. Data loaders do not. Pinning is therefore an explicit conversion workflow rather than part of the ordinary `update` and `build` loop.
