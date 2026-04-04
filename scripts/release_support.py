from __future__ import annotations

from pathlib import Path
import re
import tomllib


def read_project_version(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as handle:
        data = tomllib.load(handle)
    return data["project"]["version"]


def extract_changelog_section(changelog_text: str, version: str) -> str | None:
    match = re.search(rf"^## {re.escape(version)}\s*$", changelog_text, re.MULTILINE)
    if match is None:
        return None

    next_section = re.search(r"^## ", changelog_text[match.end() :], re.MULTILINE)
    if next_section is None:
        return changelog_text[match.end() :].strip()
    return changelog_text[match.end() : match.end() + next_section.start()].strip()


def validate_changelog(changelog_path: Path, version: str) -> None:
    if not changelog_path.exists():
        raise FileNotFoundError(f"Missing changelog file: {changelog_path}")

    changelog_text = changelog_path.read_text()
    section = extract_changelog_section(changelog_text, version)
    if section is None:
        raise ValueError(f"CHANGELOG.md is missing a '## {version}' entry.")

    bullet_lines = [
        line.strip()
        for line in section.splitlines()
        if line.strip().startswith("- ") or line.strip().startswith("* ")
    ]
    if not bullet_lines:
        raise ValueError(f"CHANGELOG.md entry for {version} must contain at least one bullet.")


def ensure_release_branch(branch: str, *, release_branch: str = "main") -> None:
    if branch != release_branch:
        raise ValueError(f"Releases must run from '{release_branch}', found '{branch}'.")


def ensure_clean_worktree(status_output: str, *, context: str) -> None:
    if status_output.strip():
        raise ValueError(f"Git worktree must be clean {context}.")


def dirty_paths(status_output: str) -> list[str]:
    paths: list[str] = []
    for line in status_output.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path)
    return paths
