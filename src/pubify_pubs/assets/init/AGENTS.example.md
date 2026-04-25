# AGENTS.md

## First Reads
- If this workspace includes a local `pubify-pubs` checkout, read:
  - `pubify-pubs/AGENTS.md`
  - `pubify-pubs/README.md`
- Otherwise, use the public `pubify-pubs` docs:
  - https://nvnunes.github.io/pubify-pubs/
  - https://nvnunes.github.io/pubify-pubs/architecture/

## Working Rules
- Keep publication-specific science code, pinned scientific data, manuscript-local helpers, and TeX sources in the host publication workspace.
- Use `pubs init <publication-id>` to initialize or repair publication scaffolding.
- Generated TeX artifacts live under each publication's `data/tex-artifacts/` tree and are exposed to TeX through local symlinks.
