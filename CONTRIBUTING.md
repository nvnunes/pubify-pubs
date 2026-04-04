# Contributing

This document is for human contributors working on `pubify-pubs`.

For package usage, examples, and CLI behavior, see `README.md`.
For release history, see `CHANGELOG.md`.

## Development Setup

`pubify-pubs` targets Python 3.10+ and expects a working LaTeX installation for build and publication-validation workflows.

Install the project with development dependencies:

```bash
./.conda/bin/pip install -e ".[dev]"
```

If you are not using the repo-local `.conda` environment, install the same extras into your own environment.

## Local Checks

The canonical full test command is:

```bash
./.conda/bin/pytest tests -q
```

The repo also has a local pre-commit hook:

```bash
sh .githooks/pre-commit
```

That hook is not just linting. It regenerates the tracked docs artifact:

- `site/`

It may rewrite that directory even if you did not edit docs directly.

A practical verification sequence after nontrivial changes is:

1. Run the full pytest command.
2. Run `sh .githooks/pre-commit`.

## Generated Artifacts

These tracked files are generated and should not be edited by hand:

- `site/`

Refresh them with:

- `./.conda/bin/mkdocs build --strict`

## Release Process

Releases are standardized around the checked-in script:

```bash
./.conda/bin/python3.12 scripts/release.py
```

This is the canonical release path. It performs the full release flow and aborts immediately if any requirement is not met.

### Branch Flow

The intended branch workflow is:

1. develop new work on `develop`
2. fast-forward `main` to the intended release state
3. run the release from `main`
4. fast-forward `develop` back to the released `main` state

The release script itself must run from `main`, but the normal development branch is `develop`.

### Before Running the Release Script

Make the release edits manually first:

1. Update `pyproject.toml` with the new version.
2. Add the matching version entry to `CHANGELOG.md`.

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

### What the Release Script Does

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
./.conda/bin/python3.12 scripts/release.py --config-file /path/to/config
```

## Notes

`AGENTS.md` is reserved for repo-specific notes that help coding agents and new threads avoid mistakes. It should stay focused on non-obvious conventions, not general contributor workflow.
