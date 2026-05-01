# Development Setup

This document covers local setup and daily workflow. For repo boundaries and
package ownership, use `architecture.md`. For canonical verification commands
and completion expectations, use `testing.md`. For contributor and release
workflow, use `CONTRIBUTING.md`.

## Shared Skills

For Python code changes, use `$python-code-writing` alongside this project's
local environment and workflow rules.

Repo-local environment setup, toolchain choices, daily commands, and hook
behavior in this document remain the source of truth for this repo.

## Environment

- target Python 3.10+
- use the repo-local `./.conda` environment by default
- keep a working LaTeX installation available for build and
  publication-validation workflows

The repo does not currently ship a dedicated bootstrap script. For a fresh
clone, create the local environment with:

```bash
conda create -p ./.conda python=3.12 pip -y
```

Then install the package and dev dependencies with:

```bash
./.conda/bin/pip install -e ".[dev]"
```

Then activate the versioned git hook path with:

```bash
git config core.hooksPath .githooks
```

If a task explicitly requires another environment, keep the dependency set and
commands equivalent to the local `./.conda` workflow.

## Daily Commands

Prefer commands from the local environment instead of bare `python`, `pip`, or
`mkdocs` invocations:

```bash
./.conda/bin/pip install -e ".[dev]"
./.conda/bin/pytest tests -q
./.conda/bin/mkdocs build --strict
sh .githooks/pre-commit
```

Use the installed CLI from the same environment when checking the local command
surface:

```bash
./.conda/bin/pubs --help
```

## Docs And Git Hooks

The MkDocs site is part of the normal local workflow.

- `site/` is a tracked generated artifact
- `./.conda/bin/mkdocs build --strict` is the supported docs build
- `.githooks/pre-commit` rebuilds `site/` and stages it after running tests

If hooks are not active in your clone, run:

```bash
git config core.hooksPath .githooks
```

## Release Workflow Pointer

Use `CONTRIBUTING.md` for branch flow, changelog requirements, the release
script, and PyPI upload workflow.
