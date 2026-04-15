# Contributing

This document is for human contributors working on `pubify-pubs`.

For package usage, examples, and CLI behavior, see `README.md`.
For repo structure and ownership rules, see `docs/architecture.md`.
For local setup and daily commands, see `docs/development.md`.
For canonical verification commands and completion expectations, see `docs/testing.md`.
For release history, see `CHANGELOG.md`.

## Contributor Workflow

- Keep publication-specific science code, manuscript-local helpers, and pinned scientific data in host publications rather than in this package.
- Use the repo-local `./.conda` environment and the commands in `docs/development.md` unless a task explicitly requires something else.
- Finish substantial work with the verification path in `docs/testing.md`.
- Do not hand-edit `site/`; rebuild it through the docs workflow or the pre-commit hook.

## Release Process

Releases are standardized around the checked-in script:

```bash
./.conda/bin/python scripts/release.py
```

This is the canonical release path. It performs the full release flow and aborts immediately if any requirement is not met.

### Branch Flow

The intended branch workflow is:

1. develop new work on `develop`
2. fast-forward `main` to the intended release state
3. run the release from `main`
4. fast-forward `develop` back to the released `main` state

The release script itself must run from `main`, but the normal development branch is `develop`.

### Before Running The Release Script

Make the release edits manually first:

1. update `pyproject.toml` with the new version
2. add the matching version entry to `CHANGELOG.md`

The changelog format is:

```md
## 1.0.1

- User-visible change one.
- User-visible change two.
```

Each release entry must:

- match the version in `pyproject.toml`
- use a `## <version>` heading
- contain at least one non-empty bullet

Before running the release script, satisfy the verification expectations in `docs/testing.md`.

### What The Release Script Does

The script requires:

- you are on `main`
- the worktree is clean before starting
- `CHANGELOG.md` contains a non-empty entry for the current version
- the release tag does not already exist
- a Twine config file is available

It then runs, in order:

1. full pytest
2. `sh .githooks/pre-commit`
3. a clean-worktree check again
4. a fresh sdist/wheel build
5. `twine check`
6. `git tag v<version>`
7. `git push origin main`
8. `git push origin v<version>`
9. `twine upload`

The script builds fresh artifacts for that run and uploads only those artifacts.

Because the pre-commit hook regenerates tracked outputs, the release script restores the known generated hook outputs from `HEAD` before its final clean-worktree check. Any remaining changes after that are treated as a real release blocker.

### PyPI Credentials

By default, the release script uses:

```text
~/.pypirc-pubify-pubs
```

You can override that path with:

```bash
./.conda/bin/python scripts/release.py --config-file /path/to/config
```
