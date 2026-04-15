# AGENTS.md

## Astro-Agents Bootstrap
- Use `astro-agents` for reusable authoring, review, and routing guidance in this repo.

## Scope
- Documentation surface profile: public-python.

## Source Of Truth Docs
- Follow `README.md` for the public overview and starting docs.
- Follow `docs/architecture.md` for workspace/package boundaries, supported CLI and Python API contracts, generated artifact ownership, and publication-facing conventions that must remain stable.
- Follow `docs/testing.md` for canonical verification commands and completion expectations.
- Follow `docs/development.md` for local `./.conda` usage, LaTeX prerequisites, docs workflow, and git hook activation.
- Follow `CONTRIBUTING.md` for contributor and release workflow.

## Shared Guidance
- Use `astro-agents/guidance/agent-surface.md` for shared agent-surface guidance.
- Use `astro-agents/guidance/public-python-projects.md` for shared public Python repo guidance.
- Use `astro-agents/guidance/python-development.md` for shared Python architecture, coding-policy, and development-workflow guidance.

## Authoring Requirements
- For Python code, follow `astro-agents/authoring/code/python.md`.
- For repo docs such as `docs/architecture.md`, `docs/testing.md`, `docs/development.md`, and similar long-lived repo documents, follow `astro-agents/authoring/writing/repo-docs.md`.
- For `README.md`, follow `astro-agents/authoring/writing/readme-md.md` in addition to `astro-agents/authoring/writing/repo-docs.md`.
- For plan documents or phased execution docs when they are created or revised, follow `astro-agents/authoring/writing/plan.md`.

## Working Rules
- For package boundaries, public CLI/API contracts, generated artifact ownership, and workflow-sensitive changes, consult `docs/architecture.md` before editing.
- Before concluding substantial work, satisfy the verification expectations in `docs/testing.md`.
- Use the local `./.conda` environment and the workflow in `docs/development.md` for Python commands, test runs, and docs builds unless a task explicitly requires something else.
- Keep publication-specific science code, pinned scientific data, and manuscript-local helpers out of this package.

## Review Lens
- Favor package/workspace ownership clarity, lifecycle clarity, conservative public-surface changes, and removal of stale abstractions over preserving weak indirection.
