from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
import difflib
import hashlib
import shutil
import subprocess
from collections.abc import Callable

from pubify_pubs.config import SYNC_STATE_FILENAME, dump_sync_state, load_sync_state
from pubify_pubs.discovery import PublicationDefinition
from pubify_pubs.stats import AUTOSTATS_FILENAME


@dataclass(frozen=True)
class DiffEntry:
    """Diff status and optional unified diff for one managed publication path."""

    path: str
    status: str
    diff: str | None


@dataclass(frozen=True)
class PullResult:
    """Outcome details from one pull operation."""

    warnings: tuple[str, ...]
    forced_paths: tuple[str, ...]


@dataclass(frozen=True)
class PushResult:
    """Outcome details from one push operation."""

    forced_paths: tuple[str, ...]


@dataclass(frozen=True)
class FileState:
    path: str
    local_hash: str | None
    mirror_hash: str | None
    synced_hash: str | None
    status: str


KDiff3Runner = Callable[[list[str]], None]


def push_publication(
    publication: PublicationDefinition,
    git: object | None = None,
    *,
    force: bool = False,
) -> PushResult:
    """Copy managed local TeX sources and generated figures to the mirror."""

    del git
    mirror_root = _require_mirror_root(publication)
    manifest = _load_shared_manifest(publication.paths.tex_root, mirror_root, require_match=True)
    initial_sync = _is_initial_sync(publication.paths.tex_root, mirror_root)
    states = _collect_file_states(
        publication.paths.tex_root,
        mirror_root,
        manifest,
        publication.config.sync_excludes,
    )
    forced_paths = _conflicting_paths(states)
    _abort_on_conflicting(states, "push", force=force)

    for state in states:
        if state.status == "conflicting" and state.local_hash is not None and force:
            _copy_file(publication.paths.tex_root / state.path, mirror_root / state.path)
            continue
        if state.status in {"local-only", "local-changed"} and state.local_hash is not None:
            _copy_file(publication.paths.tex_root / state.path, mirror_root / state.path)

    _deliver_figures(publication.paths.autofigures_root, mirror_root / "autofigures")
    _deliver_autostats(publication.paths.autostats_path, mirror_root / AUTOSTATS_FILENAME)
    next_manifest = _next_manifest_for_push(states, initial_sync=initial_sync, force=force)
    _write_sync_manifest(publication.paths.tex_root, next_manifest)
    _write_sync_manifest(mirror_root, next_manifest)
    _refresh_sync_base(
        publication.paths.sync_base_root,
        next_manifest,
        publication.paths.tex_root,
        mirror_root,
    )
    return PushResult(forced_paths=tuple(forced_paths if force else ()))


def pull_publication(
    publication: PublicationDefinition,
    *,
    force: bool = False,
) -> PullResult:
    """Copy managed mirror TeX sources into the local publication without deletions."""

    mirror_root = _require_mirror_root(publication)
    manifest = _load_shared_manifest(publication.paths.tex_root, mirror_root, require_match=True)
    initial_sync = _is_initial_sync(publication.paths.tex_root, mirror_root)
    states = _collect_file_states(
        publication.paths.tex_root,
        mirror_root,
        manifest,
        publication.config.sync_excludes,
    )
    forced_paths = _conflicting_paths(states)
    _abort_on_conflicting(states, "pull", force=force)

    warnings: list[str] = []
    for state in states:
        if state.status == "conflicting" and state.mirror_hash is not None and force:
            _copy_file(mirror_root / state.path, publication.paths.tex_root / state.path)
            continue
        if state.status == "mirror-only" and state.mirror_hash is not None and state.local_hash is None:
            _copy_file(mirror_root / state.path, publication.paths.tex_root / state.path)
            continue
        if state.status == "mirror-changed" and state.mirror_hash is not None:
            _copy_file(mirror_root / state.path, publication.paths.tex_root / state.path)
            continue
        if state.status == "local-only":
            warnings.append(f"Local-only file kept during pull: {state.path}")
            continue
        if state.status == "local-changed":
            warnings.append(f"Local-changed file kept during pull: {state.path}")

    next_manifest = _next_manifest_for_pull(states, initial_sync=initial_sync, force=force)
    _write_sync_manifest(publication.paths.tex_root, next_manifest)
    _write_sync_manifest(mirror_root, next_manifest)
    _refresh_sync_base(
        publication.paths.sync_base_root,
        next_manifest,
        publication.paths.tex_root,
        mirror_root,
    )
    return PullResult(warnings=tuple(warnings), forced_paths=tuple(forced_paths if force else ()))


def diff_publication(
    publication: PublicationDefinition,
    relative_path: str | None = None,
) -> list[DiffEntry]:
    """Compare managed local TeX files, mirror files, and the last sync manifest."""

    mirror_root = _require_mirror_root(publication)
    manifest = _load_shared_manifest(publication.paths.tex_root, mirror_root, require_match=False)
    states = _collect_file_states(
        publication.paths.tex_root,
        mirror_root,
        manifest,
        publication.config.sync_excludes,
    )

    if relative_path is not None:
        normalized = _normalize_relative_path(relative_path)
        state_map = {state.path: state for state in states}
        if normalized not in state_map:
            raise ValueError(f"Managed tex path not found: {normalized}")
        states = [state_map[normalized]]

    return [
        DiffEntry(
            path=state.path,
            status=state.status,
            diff=_maybe_diff(state.path, publication.paths.tex_root, mirror_root, state.status),
        )
        for state in states
    ]


def merge_conflicting_file(
    publication: PublicationDefinition,
    relative_path: str,
    *,
    runner: KDiff3Runner | None = None,
) -> None:
    """Launch ``kdiff3`` for one conflicting managed file using the sync-base snapshot."""

    entry = diff_publication(publication, relative_path)[0]
    if entry.status != "conflicting":
        return

    base_path = publication.paths.sync_base_root / relative_path
    if not base_path.exists():
        raise RuntimeError(f"Missing sync-base snapshot for conflicting file: {base_path}")

    mirror_root = _require_mirror_root(publication)
    local_path = publication.paths.tex_root / relative_path
    mirror_path = mirror_root / relative_path
    if not mirror_path.exists():
        raise RuntimeError(f"Missing mirror file for conflicting merge: {mirror_path}")

    run_kdiff3 = runner or _run_kdiff3
    try:
        run_kdiff3(
            [
                "kdiff3",
                base_path.as_posix(),
                local_path.as_posix(),
                mirror_path.as_posix(),
                "-o",
                local_path.as_posix(),
            ]
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Could not find kdiff3 on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"kdiff3 failed with exit code {exc.returncode}") from exc
def _require_mirror_root(publication: PublicationDefinition) -> Path:
    mirror_root = publication.config.mirror_root_path
    if mirror_root is None:
        raise ValueError(
            f"Publication '{publication.publication_id}' does not define mirror_root in pub.yaml. "
            "Set mirror_root before using push, pull, or diff."
        )
    if not mirror_root.exists():
        raise ValueError(f"Mirror does not exist: {mirror_root}")
    return mirror_root


def _load_shared_manifest(
    local_root: Path,
    mirror_root: Path,
    *,
    require_match: bool,
) -> dict[str, str]:
    local_manifest = load_sync_state(local_root / SYNC_STATE_FILENAME)
    mirror_manifest = load_sync_state(mirror_root / SYNC_STATE_FILENAME)
    if local_manifest and mirror_manifest and local_manifest != mirror_manifest:
        if require_match:
            raise RuntimeError(
                "Local and mirror sync manifests differ. Resolve the sync state before running sync commands."
            )
        return _merge_manifests_for_diff(local_manifest, mirror_manifest)
    return local_manifest or mirror_manifest


def _merge_manifests_for_diff(
    local_manifest: dict[str, str],
    mirror_manifest: dict[str, str],
) -> dict[str, str]:
    merged = dict(mirror_manifest)
    merged.update(local_manifest)
    return merged


def _is_initial_sync(local_root: Path, mirror_root: Path) -> bool:
    return not (local_root / SYNC_STATE_FILENAME).exists() and not (mirror_root / SYNC_STATE_FILENAME).exists()


def _collect_file_states(
    local_root: Path,
    mirror_root: Path,
    manifest: dict[str, str],
    excludes: tuple[str, ...],
) -> list[FileState]:
    local_hashes = _managed_hashes(local_root, excludes)
    mirror_hashes = _managed_hashes(mirror_root, excludes)
    paths = sorted(set(local_hashes) | set(mirror_hashes) | set(manifest))
    return [
        FileState(
            path=rel_path,
            local_hash=local_hashes.get(rel_path),
            mirror_hash=mirror_hashes.get(rel_path),
            synced_hash=manifest.get(rel_path),
            status=_classify_status(
                local_hashes.get(rel_path),
                mirror_hashes.get(rel_path),
                manifest.get(rel_path),
            ),
        )
        for rel_path in paths
    ]


def _next_manifest_for_push(
    states: list[FileState],
    *,
    initial_sync: bool,
    force: bool,
) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for state in states:
        synced_hash = _synced_hash_after_push(state, initial_sync=initial_sync, force=force)
        if synced_hash is not None:
            manifest[state.path] = synced_hash
    return manifest


def _next_manifest_for_pull(
    states: list[FileState],
    *,
    initial_sync: bool,
    force: bool,
) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for state in states:
        synced_hash = _synced_hash_after_pull(state, initial_sync=initial_sync, force=force)
        if synced_hash is not None:
            manifest[state.path] = synced_hash
    return manifest


def _synced_hash_after_push(state: FileState, *, initial_sync: bool, force: bool) -> str | None:
    if state.status == "unchanged":
        return _adopted_hash(state, initial_sync=initial_sync)
    if state.status == "local-only":
        return state.local_hash
    if state.status == "mirror-only":
        return _adopted_hash(state, initial_sync=initial_sync)
    if state.status == "local-changed":
        return state.local_hash
    if state.status == "mirror-changed":
        return state.synced_hash
    if state.status == "in-sync":
        return state.local_hash
    if state.status == "conflicting" and force:
        return state.local_hash
    raise RuntimeError(f"Unexpected push state for {state.path}: {state.status}")


def _synced_hash_after_pull(state: FileState, *, initial_sync: bool, force: bool) -> str | None:
    if state.status == "unchanged":
        return _adopted_hash(state, initial_sync=initial_sync)
    if state.status == "local-only":
        return _adopted_hash(state, initial_sync=initial_sync)
    if state.status == "mirror-only":
        if state.local_hash is None and state.mirror_hash is not None:
            return state.mirror_hash
        return _adopted_hash(state, initial_sync=initial_sync)
    if state.status == "local-changed":
        return state.synced_hash
    if state.status == "mirror-changed":
        return state.mirror_hash
    if state.status == "in-sync":
        return state.local_hash
    if state.status == "conflicting" and force:
        return state.mirror_hash
    raise RuntimeError(f"Unexpected pull state for {state.path}: {state.status}")


def _adopted_hash(state: FileState, *, initial_sync: bool) -> str | None:
    if state.synced_hash is not None:
        return state.synced_hash
    if not initial_sync:
        return None
    if state.local_hash is not None:
        return state.local_hash
    return state.mirror_hash


def _abort_on_conflicting(states: list[FileState], direction: str, *, force: bool) -> None:
    if force:
        return
    conflicting = _conflicting_paths(states)
    if not conflicting:
        return
    examples = ", ".join(conflicting[:5])
    raise RuntimeError(
        f"Conflicting local and mirror changes block {direction}. Example paths: {examples}"
    )


def _conflicting_paths(states: list[FileState]) -> list[str]:
    return [state.path for state in states if state.status == "conflicting"]


def _managed_hashes(root: Path, excludes: tuple[str, ...]) -> dict[str, str]:
    return {
        rel_path: _file_hash(root / rel_path)
        for rel_path in _managed_relative_paths(root, excludes)
    }


def _managed_relative_paths(root: Path, excludes: tuple[str, ...]) -> list[str]:
    if not root.exists():
        return []

    rel_paths: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root).as_posix()
        if rel_path == SYNC_STATE_FILENAME:
            continue
        if _is_excluded(rel_path, excludes):
            continue
        rel_paths.append(rel_path)
    return rel_paths


def _classify_status(
    local_hash: str | None,
    mirror_hash: str | None,
    synced_hash: str | None,
) -> str:
    if local_hash is not None and mirror_hash is None:
        return "local-only"
    if local_hash is None and mirror_hash is not None:
        return "mirror-only"
    local_changed = local_hash != synced_hash
    mirror_changed = mirror_hash != synced_hash
    if not local_changed and not mirror_changed:
        return "unchanged"
    if local_changed and not mirror_changed:
        return "local-changed"
    if not local_changed and mirror_changed:
        return "mirror-changed"
    if local_hash == mirror_hash:
        return "in-sync"
    return "conflicting"


def _normalize_relative_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"Diff path must be relative to tex/: {value}")
    normalized = path.as_posix()
    if normalized in {"", "."} or any(part == ".." for part in path.parts):
        raise ValueError(f"Diff path must stay under tex/: {value}")
    return normalized


def _is_excluded(rel_path: str, excludes: tuple[str, ...]) -> bool:
    if rel_path == ".DS_Store" or rel_path.endswith("/.DS_Store"):
        return True
    if rel_path == ".vscode" or rel_path.startswith(".vscode/"):
        return True
    if rel_path.startswith("build/"):
        return True
    if rel_path.startswith("autofigures/"):
        return True
    if rel_path == AUTOSTATS_FILENAME:
        return True
    if rel_path.startswith(".pubs-sync-base/"):
        return True
    return any(fnmatch(rel_path, pattern) for pattern in excludes)


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_file(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source.read_bytes())


def _deliver_figures(local_autofigures_root: Path, mirror_autofigures_root: Path) -> None:
    if not local_autofigures_root.exists():
        return
    for source in sorted(local_autofigures_root.rglob("*")):
        if not source.is_file():
            continue
        rel_path = source.relative_to(local_autofigures_root)
        _copy_file(source, mirror_autofigures_root / rel_path)


def _deliver_autostats(local_autostats_path: Path, mirror_autostats_path: Path) -> None:
    if not local_autostats_path.exists():
        return
    _copy_file(local_autostats_path, mirror_autostats_path)


def _refresh_sync_base(
    sync_base_root: Path,
    manifest: dict[str, str],
    local_root: Path,
    mirror_root: Path,
) -> None:
    if sync_base_root.exists():
        shutil.rmtree(sync_base_root)
    sync_base_root.mkdir(parents=True, exist_ok=True)
    for rel_path in sorted(manifest):
        source = local_root / rel_path
        if not source.exists():
            source = mirror_root / rel_path
        if not source.exists():
            raise RuntimeError(f"Missing synced source content for sync-base refresh: {rel_path}")
        _copy_file(source, sync_base_root / rel_path)


def _run_kdiff3(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _write_sync_manifest(root: Path, manifest: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / SYNC_STATE_FILENAME).write_text(dump_sync_state(manifest), encoding="utf-8")


def _maybe_diff(
    rel_path: str,
    local_root: Path,
    mirror_root: Path,
    status: str,
) -> str | None:
    if status != "conflicting":
        return None
    local_bytes = _bytes_or_none(local_root / rel_path)
    mirror_bytes = _bytes_or_none(mirror_root / rel_path)
    if any(value is not None and _is_binary(value) for value in (local_bytes, mirror_bytes)):
        return None

    local_text = "" if local_bytes is None else local_bytes.decode("utf-8")
    mirror_text = "" if mirror_bytes is None else mirror_bytes.decode("utf-8")
    diff_lines = difflib.unified_diff(
        mirror_text.splitlines(),
        local_text.splitlines(),
        fromfile=f"mirror/{rel_path}",
        tofile=f"local/{rel_path}",
        lineterm="",
    )
    return "\n".join(diff_lines)


def _bytes_or_none(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _is_binary(content: bytes) -> bool:
    return b"\0" in content
