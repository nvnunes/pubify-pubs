# AGENTS.md

## Scope
- Documentation surface profile: public-python.

## Source Of Truth Docs
- Follow `README.md` for the public overview and starting docs.
- Follow `docs/architecture.md` for workspace/package boundaries, supported CLI and Python API contracts, generated artifact ownership, and publication-facing conventions that must remain stable.
- Follow `docs/testing.md` for canonical verification commands and completion expectations.
- Follow `docs/development.md` for local `./.conda` usage, LaTeX prerequisites, docs workflow, and git hook activation.
- Follow `CONTRIBUTING.md` for contributor and release workflow.

## Shared Validation
- Use `$agent-surface-review` for shared agent-surface review.
- Use `$documentation-surface-review` for documentation-surface review with the `public-python` profile.
- Use `$code-quality-review` for source-code quality review.

## Skill Requirements
- For Python code, use `$python-code-writing`.
- For project docs such as `docs/architecture.md`, `docs/testing.md`, `docs/development.md`, and similar long-lived project documents, use `$project-docs-writing`.
- For `README.md`, use `$readme-writing`.
- For plan documents or phased execution docs when they are created or revised, use `$plan-writing`.

## Astro-Agents Integration
- `astro-agents` owns the shared `$pubify-authoring` skill used by agents working on downstream pubify publication and presentation workflows.
- When this project changes user-facing `pubify-pubs` behavior, update the corresponding `astro-agents` skill references: `skills/pubify-authoring/references/pubify-pubs.md`, `skills/pubify-authoring/references/pubify-pubs-figures.md`, and shared references when the change affects `figures.py`, data loaders, source reuse, or figure export behavior.
- Examples that require an `astro-agents` update include CLI changes, `pub.yaml` schema changes, generated artifact behavior, `FigureResult` or `panel(...)` API changes, data/source reuse changes, validation behavior, and authoring workflow changes.

## Working Rules
- For package boundaries, public CLI/API contracts, generated artifact ownership, and workflow-sensitive changes, consult `docs/architecture.md` before editing.
- Before concluding substantial work, satisfy the verification expectations in `docs/testing.md`.
- Use the local `./.conda` environment and the workflow in `docs/development.md` for Python commands, test runs, and docs builds unless a task explicitly requires something else.
- Keep publication-specific science code, pinned scientific data, and manuscript-local helpers out of this package.

## Review Lens
- Favor package/workspace ownership clarity, lifecycle clarity, conservative public-surface changes, and removal of stale abstractions over preserving weak indirection.
