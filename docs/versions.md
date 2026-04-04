# Versions

`pubify-pubs` can snapshot the publication-local TeX tree and build redline PDFs with `latexdiff`.

These workflows are intentionally publication-local. They do not replace Git and they do not try to snapshot Python code, environments, or external scientific inputs.

## Commands

- `pubs <publication-id> version list`
- `pubs <publication-id> version create [note]`
- `pubs <publication-id> version create undo`
- `pubs <publication-id> version diff <version-id>`
- `pubs <publication-id> version diff <version-id> <version-id>`

## Snapshot Layout

Snapshots live under:

```text
papers/<publication-id>/tex/versions/
  metadata.yaml
  v1/
  v2/
  ...
```

`metadata.yaml` records:

- version id such as `v1`
- creation time
- optional note
- `main_tex`

Each `vN/` snapshot stores the non-build TeX tree for that version.

Included:

- manuscript `.tex`
- `.bib`
- `.sty`
- package-owned TeX support files
- generated outputs such as `autofigures/`, `autostats.tex`, and `autotables.tex`

Excluded:

- `build/`
- `versions/`
- LaTeX build artifacts such as `.aux`, `.log`, `.fls`, `.fdb_latexmk`, and similar files

## Create And Undo

Create the next snapshot:

```bash
pubs my-paper version create "First full draft"
```

If the newest stored snapshot is identical to the current live `tex/` state, you can remove it with:

```bash
pubs my-paper version create undo
```

Undo only applies to the most recent stored version.

## Diff And Redline PDFs

Compare one stored version to the current live tree:

```bash
pubs my-paper version diff v1
```

Compare two stored versions:

```bash
pubs my-paper version diff v1 v2
pubs my-paper version diff v2 v1
```

When two stored versions are given, pubify normalizes internally to older -> newer.

The redline workflow:

1. prepares a temporary TeX tree
2. overlays newer assets on older assets
3. runs `latexdiff` on the two `main_tex` entrypoints
4. runs `latexmk` on the diffed entrypoint
5. copies the resulting PDF into the live `tex/build/` directory

Output PDFs use the standardized names:

- `<main-stem>-diff-v<older>-v<newer>.pdf`
- `<main-stem>-diff-v<older>-current.pdf`

Examples:

- `main-diff-v1-v2.pdf`
- `main-diff-v1-current.pdf`

## Scope

Versions are snapshots of the publication-local TeX state. They are useful for:

- pinning major manuscript milestones
- building readable redline PDFs
- keeping the version workflow local to the publication even when Git is not the primary manuscript tool

They are not intended to be full reproducibility snapshots of `figures.py`, Python environments, or external code.
