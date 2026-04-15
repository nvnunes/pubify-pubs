# Testing

This document is the source of truth for local verification commands and
completion expectations in `pubify-pubs`.

## Shared Validation

Use the shared base testing guidance in
`astro-agents/validation/base-testing.md`.

## Environment

Use the repo-local `./.conda` environment for Python commands, test runs, and
docs builds unless a task explicitly requires something else.

Build and publication-validation workflows also expect a working LaTeX
installation. If exported figures use LaTeX text rendering through
`pubify-mpl`, LaTeX must be available during Python-side export as well.

## Canonical Verification Commands

Run the package test suite with:

```bash
./.conda/bin/pytest tests -q
```

Build the docs with:

```bash
./.conda/bin/mkdocs build --strict
```

Run the repo-local pre-commit hook with:

```bash
sh .githooks/pre-commit
```

## Important Test Surfaces

- `tests/test_cli_core.py`
  - main workflow contract suite covering config loading, init, check, export,
    build, shell behavior, pinning, sync, and diff
- `tests/test_export.py`
  - focused export and config-normalization coverage
- `tests/test_release.py`
  - release-script and changelog validation coverage
- `tests/test_stats.py` and `tests/test_tables.py`
  - focused stats and table behavior coverage

## Completion Expectations

Run the full test suite before concluding substantial code changes.

Always finish with the full package test suite for changes that affect:

- config loading or workspace discovery
- publication discovery or validation
- export, build, or runtime orchestration
- CLI semantics or shell behavior
- publication bootstrap templates
- public helper behavior or generated-output semantics

Run the strict docs build for changes that affect:

- `README.md`
- `docs/*`
- `CONTRIBUTING.md`
- `AGENTS.md`
- `mkdocs.yml`
- package metadata or docstrings that feed the public docs surface

Run the repo-local pre-commit hook whenever docs or package behavior changed,
because it rebuilds and stages the tracked `site/` artifact.

Targeted tests are acceptable during iteration, but final verification should
match the changed surface area.

## Git Hook Behavior

The repo includes a versioned pre-commit hook at `.githooks/pre-commit`.

When active, it runs:

- `./.conda/bin/pytest tests -q`
- `./.conda/bin/mkdocs build --strict`

If the hooks path is not active in your clone, set it with:

```bash
git config core.hooksPath .githooks
```

The hook also stages `site/` after rebuilding it.

## Generated Artifacts

These tracked files are generated and should not be edited by hand:

- `site/`
