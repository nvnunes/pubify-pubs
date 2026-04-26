#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile

from release_support import (
    dirty_paths,
    ensure_clean_worktree,
    ensure_release_branch,
    read_project_version,
    validate_changelog,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
DEFAULT_TWINE_CONFIG = Path.home() / ".pypirc-pubify-pubs"
GENERATED_ARTIFACT_PATHS = ("site",)


def _print_step(index: int, message: str) -> None:
    print(f"[{index}] {message}")


def _run(cmd: list[str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    print(f"+ {' '.join(shlex.quote(part) for part in cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=True, text=True)


def _capture(cmd: list[str], *, cwd: Path = REPO_ROOT) -> str:
    result = subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _git_status() -> str:
    return _capture(["git", "status", "--porcelain"])


def _git_branch() -> str:
    return _capture(["git", "branch", "--show-current"])


def _tag_exists(tag_name: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag_name}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _restore_generated_artifacts() -> None:
    _run(["git", "checkout", "HEAD", "--", *GENERATED_ARTIFACT_PATHS])


def _clean_build_tree() -> None:
    build_root = REPO_ROOT / "build"
    if build_root.exists():
        shutil.rmtree(build_root)


def _build_artifacts(version: str) -> list[Path]:
    out_dir = Path(tempfile.mkdtemp(prefix=f"pubify_release_{version.replace('.', '_')}_"))
    _clean_build_tree()
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(out_dir),
        ]
    )
    artifacts = sorted(out_dir.glob(f"pubify_pubs-{version}*"))
    if not artifacts:
        raise RuntimeError(f"No build artifacts were produced for version {version}.")
    return artifacts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full pubify-pubs release flow.")
    parser.add_argument(
        "--config-file",
        type=Path,
        default=DEFAULT_TWINE_CONFIG,
        help="Path to the Twine config file to use for upload.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    version = read_project_version(PYPROJECT_PATH)
    tag_name = f"v{version}"

    _print_step(1, f"Validating release prerequisites for {version}")
    ensure_release_branch(_git_branch())
    ensure_clean_worktree(_git_status(), context="before release")
    validate_changelog(CHANGELOG_PATH, version)
    if _tag_exists(tag_name):
        raise ValueError(f"Git tag '{tag_name}' already exists.")
    if not args.config_file.exists():
        raise FileNotFoundError(f"Missing Twine config file: {args.config_file}")

    _print_step(2, "Running full test suite")
    _run([sys.executable, "-m", "pytest", "tests", "-q"])

    _print_step(3, "Running pre-commit hook")
    _run(["sh", ".githooks/pre-commit"])

    _print_step(4, "Re-checking worktree after pre-commit")
    status_after_hook = _git_status()
    if status_after_hook:
        changed_paths = dirty_paths(status_after_hook)
        generated_paths = set(GENERATED_ARTIFACT_PATHS)
        only_generated_artifacts_changed = all(
            path in generated_paths or any(path.startswith(f"{prefix}/") for prefix in generated_paths)
            for path in changed_paths
        )
        if only_generated_artifacts_changed:
            _print_step(5, "Discarding regenerated tracked artifacts from the hook")
            _restore_generated_artifacts()
            status_after_hook = _git_status()
    ensure_clean_worktree(status_after_hook, context="after pre-commit")

    _print_step(6, "Building fresh distribution artifacts")
    artifacts = _build_artifacts(version)

    _print_step(7, "Running twine check")
    _run([sys.executable, "-m", "twine", "check", *(str(path) for path in artifacts)])

    _print_step(8, f"Creating git tag {tag_name}")
    _run(["git", "tag", tag_name])

    _print_step(9, "Pushing main")
    _run(["git", "push", "origin", "main"])

    _print_step(10, f"Pushing tag {tag_name}")
    _run(["git", "push", "origin", tag_name])

    _print_step(11, "Uploading artifacts to PyPI")
    _run(
        [
            sys.executable,
            "-m",
            "twine",
            "upload",
            "--config-file",
            str(args.config_file),
            *(str(path) for path in artifacts),
        ]
    )

    print("Release complete.")
    for artifact in artifacts:
        print(f"- {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
