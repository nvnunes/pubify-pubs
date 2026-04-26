from pathlib import Path
import shutil
import sys
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from release_support import (
    dirty_paths,
    ensure_clean_worktree,
    ensure_release_branch,
    read_project_version,
    validate_changelog,
)
import release


def test_repo_changelog_matches_current_project_version() -> None:
    version = read_project_version(REPO_ROOT / "pyproject.toml")

    validate_changelog(REPO_ROOT / "CHANGELOG.md", version)


def test_validate_changelog_accepts_matching_version_with_bullets(tmp_path: Path) -> None:
    pyproject_path = tmp_path / "pyproject.toml"
    changelog_path = tmp_path / "CHANGELOG.md"
    pyproject_path.write_text("[project]\nversion = \"1.2.3\"\n")
    changelog_path.write_text("# Changelog\n\n## 1.2.3\n\n- Added the release script.\n")

    version = read_project_version(pyproject_path)

    validate_changelog(changelog_path, version)


def test_validate_changelog_rejects_missing_version_heading(tmp_path: Path) -> None:
    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text("# Changelog\n\n## 1.2.2\n\n- Previous release.\n")

    with pytest.raises(ValueError, match=r"## 1\.2\.3"):
        validate_changelog(changelog_path, "1.2.3")


def test_validate_changelog_rejects_empty_version_section(tmp_path: Path) -> None:
    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text("# Changelog\n\n## 1.2.3\n\nParagraph only.\n")

    with pytest.raises(ValueError, match="at least one bullet"):
        validate_changelog(changelog_path, "1.2.3")


def test_release_branch_check_rejects_non_main() -> None:
    with pytest.raises(ValueError, match="main"):
        ensure_release_branch("develop")


def test_clean_worktree_check_rejects_dirty_status() -> None:
    with pytest.raises(ValueError, match="clean"):
        ensure_clean_worktree(" M README.md", context="before release")


def test_dirty_paths_extracts_paths_from_porcelain_output() -> None:
    status_output = "M  site/index.html\nA  dist/pubify_pubs-1.0.0-py3-none-any.whl\nR  old -> new\n"

    assert dirty_paths(status_output) == [
        "site/index.html",
        "dist/pubify_pubs-1.0.0-py3-none-any.whl",
        "new",
    ]


def test_build_artifacts_cleans_stale_build_tree_and_includes_runtime_assets() -> None:
    version = read_project_version(REPO_ROOT / "pyproject.toml")
    stale_module = REPO_ROOT / "build" / "lib" / "pubify_pubs" / "decorators.py"
    stale_module.parent.mkdir(parents=True, exist_ok=True)
    stale_module.write_text("# stale\n", encoding="utf-8")

    try:
        artifacts = release._build_artifacts(version)
        wheel_path = next(path for path in artifacts if path.suffix == ".whl")

        with zipfile.ZipFile(wheel_path) as wheel:
            names = set(wheel.namelist())

        assert "pubify_pubs/assets/init/AGENTS.example.md" in names
        assert "pubify_pubs/decorators.py" not in names
    finally:
        shutil.rmtree(REPO_ROOT / "build", ignore_errors=True)
