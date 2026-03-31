from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from pubify_pubs.config import _parse_simple_yaml
from pubify_pubs.discovery import PublicationDefinition
from pubify_pubs.runtime import CommandRunner, run_command
from pubify_pubs.texlog import build_log_path, extract_latex_diagnostic

_VERSION_METADATA_KEY = "versions"
_EXCLUDED_DIR_NAMES = {"build", "versions", ".pubs-sync-base", "__pycache__"}
_EXCLUDED_FILE_NAMES = {".pubs-sync.yaml"}
_EXCLUDED_FILE_SUFFIXES = {
    ".aux",
    ".bbl",
    ".bcf",
    ".blg",
    ".fdb_latexmk",
    ".fls",
    ".lof",
    ".log",
    ".lot",
    ".nav",
    ".out",
    ".run.xml",
    ".snm",
    ".toc",
    ".vrb",
    ".xdv",
}
_EXCLUDED_FILE_SUFFIX_PATTERNS = (".synctex.gz",)
_OPTIONAL_ARG_DIFF_COMMANDS = {
    "DIFadd",
    "DIFaddFL",
    "DIFdel",
    "DIFdelFL",
}


@dataclass(frozen=True)
class PublicationVersion:
    version_id: str
    created_at: str
    note: str
    main_tex: Path


def list_publication_versions(publication: PublicationDefinition) -> tuple[PublicationVersion, ...]:
    metadata_path = publication.paths.versions_metadata_path
    if not metadata_path.exists():
        return ()
    raw = _parse_simple_yaml(metadata_path.read_text(encoding="utf-8"))
    versions_raw = raw.get(_VERSION_METADATA_KEY, {})
    if not isinstance(versions_raw, dict):
        raise ValueError(f"{metadata_path}: versions must be a mapping")
    versions: list[PublicationVersion] = []
    for version_id, item in versions_raw.items():
        if not isinstance(version_id, str) or not _is_version_id(version_id):
            raise ValueError(f"{metadata_path}: invalid version id {version_id!r}")
        if not isinstance(item, dict):
            raise ValueError(f"{metadata_path}: version '{version_id}' metadata must be a mapping")
        created_at = item.get("created_at")
        note = item.get("note", "")
        main_tex = item.get("main_tex")
        if not isinstance(created_at, str) or not created_at:
            raise ValueError(f"{metadata_path}: version '{version_id}' missing created_at")
        if not isinstance(note, str):
            raise ValueError(f"{metadata_path}: version '{version_id}' note must be a string")
        if not isinstance(main_tex, str) or not main_tex:
            raise ValueError(f"{metadata_path}: version '{version_id}' missing main_tex")
        versions.append(
            PublicationVersion(
                version_id=version_id,
                created_at=created_at,
                note=note,
                main_tex=Path(main_tex),
            )
        )
    versions.sort(key=lambda item: _version_number(item.version_id))
    return tuple(versions)


def create_publication_version(
    publication: PublicationDefinition,
    *,
    note: str = "",
) -> PublicationVersion:
    versions = list_publication_versions(publication)
    next_number = 1 if not versions else _version_number(versions[-1].version_id) + 1
    version = PublicationVersion(
        version_id=f"v{next_number}",
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        note=note,
        main_tex=publication.config.main_tex_path,
    )
    snapshot_root = publication.paths.versions_root / version.version_id
    if snapshot_root.exists():
        raise ValueError(f"Version snapshot already exists: {snapshot_root}")
    publication.paths.versions_root.mkdir(parents=True, exist_ok=True)
    _copy_snapshot_tree(publication.paths.tex_root, snapshot_root)
    _write_versions_metadata(publication, (*versions, version))
    return version


def undo_publication_version_create(publication: PublicationDefinition) -> PublicationVersion:
    versions = list_publication_versions(publication)
    if not versions:
        raise ValueError("Cannot undo version create: no stored versions exist.")
    newest = versions[-1]
    snapshot_root = version_snapshot_path(publication, newest.version_id)
    if not _snapshot_trees_match(snapshot_root, publication.paths.tex_root):
        raise ValueError(
            f"Cannot undo version create: newest version '{newest.version_id}' differs from current tex/ state."
        )
    shutil.rmtree(snapshot_root)
    remaining_versions = tuple(version for version in versions if version.version_id != newest.version_id)
    _write_versions_metadata(publication, remaining_versions)
    if not remaining_versions and publication.paths.versions_metadata_path.exists():
        publication.paths.versions_metadata_path.unlink()
    if publication.paths.versions_root.exists() and not any(publication.paths.versions_root.iterdir()):
        publication.paths.versions_root.rmdir()
    return newest


def build_publication_version_diff(
    publication: PublicationDefinition,
    from_version_id: str,
    to_version_id: str | None = None,
    *,
    runner: CommandRunner | None = None,
) -> Path:
    versions = list_publication_versions(publication)
    version_map = {version.version_id: version for version in versions}
    older_label, older_root, older_main_tex, newer_label, newer_root, newer_main_tex = _resolve_diff_pair(
        publication,
        version_map,
        from_version_id,
        to_version_id,
    )
    if older_main_tex != newer_main_tex:
        raise ValueError(
            f"Cannot diff versions with different main_tex paths: {older_main_tex} vs {newer_main_tex}"
        )

    command_runner = runner or run_command
    temp_root = Path(tempfile.mkdtemp(prefix=f"pubify-version-diff-{publication.publication_id}-"))
    try:
        _copy_snapshot_tree(older_root, temp_root)
        _copy_snapshot_tree(newer_root, temp_root)
        diff_main_tex = temp_root / newer_main_tex
        diff_main_tex.parent.mkdir(parents=True, exist_ok=True)
        older_entrypoint = older_root / older_main_tex
        newer_entrypoint = newer_root / newer_main_tex
        if not older_entrypoint.exists():
            raise ValueError(f"Missing version entrypoint for {older_label}: {older_entrypoint}")
        if not newer_entrypoint.exists():
            raise ValueError(f"Missing version entrypoint for {newer_label}: {newer_entrypoint}")
        try:
            latexdiff_result = command_runner(
                [
                    "latexdiff",
                    str(older_entrypoint),
                    str(newer_entrypoint),
                ],
                temp_root,
            )
        except FileNotFoundError:
            raise ValueError("latexdiff is not installed or not available on PATH") from None
        except subprocess.CalledProcessError as exc:
            output = "\n".join(part for part in (exc.stdout, exc.stderr) if part).strip()
            message = f"latexdiff failed for {older_label} -> {newer_label} (exit {exc.returncode})"
            if output:
                message = f"{message}.\n{output}"
            raise ValueError(message) from None
        diff_text = _sanitize_latexdiff_output(latexdiff_result.stdout)
        diff_text = _restore_figfloat_blocks(
            diff_text,
            newer_entrypoint.read_text(encoding="utf-8"),
        )
        diff_main_tex.write_text(diff_text, encoding="utf-8")
        build_root = temp_root / "build"
        build_root.mkdir(parents=True, exist_ok=True)
        try:
            _run_latexmk_for_tree(
                temp_root,
                newer_main_tex,
                build_root,
                command_runner=command_runner,
                context_label=f"Version diff {older_label} -> {newer_label}",
            )
        except ValueError as exc:
            copied_log_path = _copy_version_diff_log(publication, build_root, newer_main_tex, older_label, newer_label)
            message = str(exc)
            if copied_log_path is not None:
                temp_log_path = build_log_path(build_root, newer_main_tex)
                message = message.replace(f"Log file: {temp_log_path}", f"Log file: {copied_log_path}")
            raise ValueError(message) from None
        built_pdf = build_root / newer_main_tex.with_suffix(".pdf").name
        if not built_pdf.exists():
            raise ValueError(f"Version diff build did not produce PDF: {built_pdf}")
        destination = (
            publication.paths.build_root
            / f"{newer_main_tex.stem}-diff-{older_label}-{newer_label}.pdf"
        )
        publication.paths.build_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built_pdf, destination)
        return destination
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def version_snapshot_path(publication: PublicationDefinition, version_id: str) -> Path:
    return publication.paths.versions_root / version_id


def _resolve_diff_pair(
    publication: PublicationDefinition,
    version_map: dict[str, PublicationVersion],
    from_version_id: str,
    to_version_id: str | None,
) -> tuple[str, Path, Path, str, Path, Path]:
    from_version = _resolve_version(version_map, publication, from_version_id)
    if to_version_id is None:
        return (
            from_version.version_id,
            version_snapshot_path(publication, from_version.version_id),
            from_version.main_tex,
            "current",
            publication.paths.tex_root,
            publication.config.main_tex_path,
        )
    to_version = _resolve_version(version_map, publication, to_version_id)
    ordered = sorted((from_version, to_version), key=lambda item: _version_number(item.version_id))
    older, newer = ordered
    return (
        older.version_id,
        version_snapshot_path(publication, older.version_id),
        older.main_tex,
        newer.version_id,
        version_snapshot_path(publication, newer.version_id),
        newer.main_tex,
    )


def _resolve_version(
    version_map: dict[str, PublicationVersion],
    publication: PublicationDefinition,
    version_id: str,
) -> PublicationVersion:
    version = version_map.get(version_id)
    if version is None:
        raise KeyError(f"Unknown version '{version_id}'")
    snapshot_root = version_snapshot_path(publication, version.version_id)
    if not snapshot_root.exists():
        raise ValueError(f"Missing snapshot for version '{version_id}': {snapshot_root}")
    return version


def _run_latexmk_for_tree(
    tex_root: Path,
    main_tex: Path,
    build_root: Path,
    *,
    command_runner: CommandRunner,
    context_label: str,
) -> subprocess.CompletedProcess[str]:
    command = [
        "latexmk",
        "-pdf",
        "-interaction=nonstopmode",
        "-halt-on-error",
        f"-outdir={build_root}",
        main_tex.as_posix(),
    ]
    try:
        return command_runner(command, tex_root)
    except FileNotFoundError:
        raise ValueError("latexmk is not installed or not available on PATH") from None
    except subprocess.CalledProcessError as exc:
        raise ValueError(_format_latexmk_failure(context_label, build_root, main_tex, exc.returncode)) from None


def _format_latexmk_failure(context_label: str, build_root: Path, main_tex: Path, exit_code: int) -> str:
    log_path = build_log_path(build_root, main_tex)
    diagnostic = extract_latex_diagnostic(log_path)
    lines = [
        f"{context_label} failed (latexmk exit {exit_code}).",
        f"Log file: {log_path}",
    ]
    if diagnostic is None:
        lines.append("LaTeX error: no LaTeX diagnostic could be extracted.")
        return "\n".join(lines)
    lines.append(f"LaTeX error: {diagnostic.summary}")
    if diagnostic.source is not None:
        lines.append(f"Source: {diagnostic.source}")
    if diagnostic.context is not None:
        lines.append(f"Context: {diagnostic.context}")
    return "\n".join(lines)


def _copy_snapshot_tree(source_root: Path, destination_root: Path) -> None:
    shutil.copytree(
        source_root,
        destination_root,
        dirs_exist_ok=True,
        ignore=_snapshot_ignore,
    )


def _snapshot_trees_match(left_root: Path, right_root: Path) -> bool:
    left_files = _snapshot_file_map(left_root)
    right_files = _snapshot_file_map(right_root)
    if left_files.keys() != right_files.keys():
        return False
    for relative_path in left_files:
        if left_files[relative_path].read_bytes() != right_files[relative_path].read_bytes():
            return False
    return True


def _snapshot_file_map(root: Path) -> dict[Path, Path]:
    files: dict[Path, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root)
        if _is_snapshot_excluded(relative_path):
            continue
        files[relative_path] = path
    return files


def _sanitize_latexdiff_output(text: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "[":
            result.append(char)
            index += 1
            continue
        close_index = _find_matching_optional_arg_end(text, index)
        if close_index is None:
            result.append(char)
            index += 1
            continue
        content = text[index + 1 : close_index]
        sanitized = _sanitize_optional_arg_content(content)
        result.append("[")
        result.append(sanitized if sanitized is not None else content)
        result.append("]")
        index = close_index + 1
    return "".join(result)


def _restore_figfloat_blocks(diff_text: str, newer_text: str) -> str:
    diff_blocks = _extract_figfloat_blocks(diff_text)
    newer_blocks = _extract_figfloat_blocks(newer_text)
    if not diff_blocks or not newer_blocks:
        return diff_text
    result: list[str] = []
    last_index = 0
    for block_index, (start, end, _block_text) in enumerate(diff_blocks):
        result.append(diff_text[last_index:start])
        if block_index < len(newer_blocks):
            result.append(newer_blocks[block_index][2])
        else:
            result.append(diff_text[start:end])
        last_index = end
    result.append(diff_text[last_index:])
    return "".join(result)


def _sanitize_optional_arg_content(content: str) -> str | None:
    stripped = content.strip()
    if not stripped.startswith("\\"):
        return None
    match = re.match(r"\\([A-Za-z]+)", stripped)
    if match is None:
        return None
    command = match.group(1)
    if command not in _OPTIONAL_ARG_DIFF_COMMANDS:
        return None
    brace_index = match.end()
    if brace_index >= len(stripped) or stripped[brace_index] != "{":
        return None
    end_index = _find_matching_brace_end(stripped, brace_index)
    if end_index is None or end_index != len(stripped) - 1:
        return None
    inner = stripped[brace_index + 1 : end_index]
    if command.startswith("DIFdel"):
        return ""
    return inner


def _find_matching_optional_arg_end(text: str, start_index: int) -> int | None:
    depth = 1
    index = start_index + 1
    while index < len(text):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _find_matching_brace_end(text: str, start_index: int) -> int | None:
    depth = 1
    index = start_index + 1
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _extract_figfloat_blocks(text: str) -> list[tuple[int, int, str]]:
    blocks: list[tuple[int, int, str]] = []
    search_index = 0
    while True:
        start = text.find(r"\figfloat", search_index)
        if start == -1:
            break
        end = _find_figfloat_block_end(text, start)
        if end is None:
            search_index = start + len(r"\figfloat")
            continue
        blocks.append((start, end, text[start:end]))
        search_index = end
    return blocks


def _find_figfloat_block_end(text: str, start_index: int) -> int | None:
    index = start_index + len(r"\figfloat")
    if index < len(text) and text[index] == "[":
        optional_end = _find_matching_optional_arg_end(text, index)
        if optional_end is None:
            return None
        index = optional_end + 1
    index = _skip_whitespace(text, index)
    if index >= len(text) or text[index] != "{":
        return None
    brace_end = _find_matching_brace_end(text, index)
    if brace_end is None:
        return None
    index = brace_end + 1
    index = _skip_whitespace(text, index)
    for _ in range(2):
        index = _skip_whitespace(text, index)
        if index < len(text) and text[index] == "[":
            optional_end = _find_matching_optional_arg_end(text, index)
            if optional_end is None:
                return None
            index = optional_end + 1
        else:
            break
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _copy_version_diff_log(
    publication: PublicationDefinition,
    build_root: Path,
    main_tex: Path,
    older_label: str,
    newer_label: str,
) -> Path | None:
    source_log_path = build_log_path(build_root, main_tex)
    if not source_log_path.exists():
        return None
    destination = publication.paths.build_root / f"{main_tex.stem}-diff-{older_label}-{newer_label}.log"
    publication.paths.build_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_log_path, destination)
    return destination


def _snapshot_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(directory) / name
        if path.is_dir() and name in _EXCLUDED_DIR_NAMES:
            ignored.add(name)
            continue
        if path.is_file() and _is_excluded_snapshot_file(name):
            ignored.add(name)
    return ignored


def _is_excluded_snapshot_file(name: str) -> bool:
    if name in _EXCLUDED_FILE_NAMES:
        return True
    if any(name.endswith(pattern) for pattern in _EXCLUDED_FILE_SUFFIX_PATTERNS):
        return True
    return Path(name).suffix in _EXCLUDED_FILE_SUFFIXES


def _is_snapshot_excluded(relative_path: Path) -> bool:
    if any(part in _EXCLUDED_DIR_NAMES for part in relative_path.parts[:-1]):
        return True
    return _is_excluded_snapshot_file(relative_path.name)


def _write_versions_metadata(
    publication: PublicationDefinition,
    versions: Sequence[PublicationVersion],
) -> None:
    metadata_path = publication.paths.versions_metadata_path
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [_VERSION_METADATA_KEY + ":"]
    for version in versions:
        lines.extend(
            [
                f"  {version.version_id}:",
                f"    created_at: {json.dumps(version.created_at)}",
                f"    note: {json.dumps(version.note)}",
                f"    main_tex: {json.dumps(version.main_tex.as_posix())}",
            ]
        )
    metadata_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _is_version_id(value: str) -> bool:
    if not value.startswith("v"):
        return False
    try:
        return int(value[1:]) > 0
    except ValueError:
        return False


def _version_number(version_id: str) -> int:
    if not _is_version_id(version_id):
        raise ValueError(f"Invalid version id: {version_id}")
    return int(version_id[1:])
