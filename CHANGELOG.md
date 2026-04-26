# Changelog

## 1.1.0

- Removed `pubify_pubs` authoring decorator re-exports; publication code must import `data`, `external_data`, `figure`, `stat`, and `table` from `pubify_data`.
- Removed legacy top-level `pubify.yaml` root keys; workspace roots must live under the `pubify-pubs` section.
- Switched LaTeX figure export and TeX support-file preparation to `pubify-tex`.

## 1.0.3

- Fixed `pubs <publication-id> figure <figure-id> update` so it no longer deletes unrelated generated figures.

## 1.0.2

- Added a shared `AGENTS.md` bootstrap under the publications root during `pubs init`.
- Renamed the stored `AGENTS` init template asset to avoid confusing other agents scanning the package source.

## 1.0.1

- Added bare `pubs init` to bootstrap a workspace with the default `pubify.yaml` config.
- Switched the workspace config filename from `pubify.conf` to `pubify.yaml`.
- Made freshly initialized publications build without requiring generated figures, stats, or tables first.

## 1.0.0

- Initial public release of `pubify-pubs`.
- Added the `pubs` CLI for workspace-rooted publication discovery, update, build, preview, and interactive shell workflows.
- Added first-class publication figures, stats, and tables with generated `tex/autofigures/`, `tex/autostats.tex`, and `tex/autotables.tex` outputs.
