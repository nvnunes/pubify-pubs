# Sync

`pubify-pubs` supports a conservative mirror workflow for publications that keep a second synced TeX tree, such as a mounted Overleaf mirror.

The local publication TeX tree is always canonical.

## Commands

- `pubs <publication-id> ignore <relative-path>`
- `pubs <publication-id> push [--force]`
- `pubs <publication-id> pull [--force]`
- `pubs <publication-id> diff [list|<relative-path>]`

## Managed Source Model

Managed source files are publication-local TeX sources under `tex/`, excluding:

- generated figures in `tex/autofigures/`
- generated stats in `tex/autostats.tex`
- generated tables in `tex/autotables.tex`
- build artifacts in `tex/build/`
- publication-local sync exclusions from `pub.yaml`

Generated figures are delivered one-way from local `tex/autofigures/` to mirror `autofigures/`. `autostats.tex` and `autotables.tex` are also delivered one-way to the mirror. None of those generated outputs are part of the hash-managed source sync model.

## Statuses

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

## Diff

`diff` compares local, mirror, and last-synced state.

Use:

```bash
pubs my-paper diff list
pubs my-paper diff tex/main.tex
```

## Push And Pull

`push` and `pull` are conservative:

- unilateral non-conflicting changes copy in the requested direction
- `conflicting` blocks directional sync unless `--force` is used
- `--force` applies directionally; it does not make sync symmetric or destructive beyond the requested direction

Use:

```bash
pubs my-paper push
pubs my-paper pull
```

## Ignore

`ignore <relative-path>` records a publication-local sync exclusion in `pub.yaml`.

The path must be relative to `tex/`.

Use:

```bash
pubs my-paper ignore sections/scratch.tex
```
