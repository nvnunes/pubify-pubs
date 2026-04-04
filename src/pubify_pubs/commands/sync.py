from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys

from pubify_pubs.config import add_sync_exclude
from pubify_pubs.discovery import PublicationDefinition
from pubify_pubs.mirror import diff_publication, merge_conflicting_file, pull_publication, push_publication

from pubify_pubs.commands.common import PublicationCommand, reject_build_flags_from_command, render_status_line


def handle_command(
    publication: PublicationDefinition,
    command: PublicationCommand,
    *,
    error: Callable[[str], None],
    use_color: bool,
    use_interactive_merge: bool,
) -> int | None:
    if command.command == "ignore":
        reject_build_flags_from_command(command, error)
        if command.force:
            error("ignore does not accept --force")
        if command.arg3 is None:
            error("ignore requires <relative-path>")
        if command.arg4 is not None:
            error("ignore accepts only <relative-path>")
        relative_path = parse_ignore_path(command.arg3)
        config_path = publication.paths.config_path
        if not config_path.exists():
            raise FileNotFoundError(f"Missing publication config: {config_path}")
        added = add_sync_exclude(config_path, relative_path)
        if added:
            print(f"{publication.publication_id}: added sync ignore {relative_path}")
        else:
            print(f"{publication.publication_id}: sync ignore already present {relative_path}")
        return 0
    if command.command == "push":
        reject_build_flags_from_command(command, error)
        force = parse_force_flag_value(command.command, command.arg3, command.arg4, command.force, error)
        result = push_publication(publication, force=force)
        for path in result.forced_paths:
            print(f"Forced overwrite: {path}", file=sys.stderr)
        print(f"{publication.publication_id}: pushed")
        return 0
    if command.command == "pull":
        reject_build_flags_from_command(command, error)
        force = parse_force_flag_value(command.command, command.arg3, command.arg4, command.force, error)
        result = pull_publication(publication, force=force)
        for warning in result.warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        for path in result.forced_paths:
            print(f"Forced overwrite: {path}", file=sys.stderr)
        print(f"{publication.publication_id}: pulled")
        return 0
    if command.command == "diff":
        reject_build_flags_from_command(command, error)
        if command.force:
            error("diff does not accept --force")
        if command.arg4 is not None:
            error("diff accepts at most one optional selector")
        summary_only = command.arg3 == "list"
        rel_path = None if summary_only else command.arg3
        entries = diff_publication(publication, rel_path)
        for entry in entries:
            print(render_status_line(entry.status, entry.path, use_color=use_color))
            if use_interactive_merge and rel_path is not None and entry.status == "conflicting":
                merge_conflicting_file(publication, entry.path)
                continue
            if not summary_only and entry.diff:
                print(entry.diff)
        return 0
    return None


def parse_force_flag_value(
    command: str,
    arg3: str | None,
    arg4: str | None,
    force_flag: bool,
    error: Callable[[str], None],
) -> bool:
    values = [value for value in (arg3, arg4) if value is not None]
    if values:
        error(f"{command} accepts only optional --force")
    return bool(force_flag)


def parse_ignore_path(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("ignore path must be a non-empty path relative to tex/")
    if any(char in value for char in "*?[]"):
        raise ValueError("ignore path must be an exact relative path, not a glob pattern")
    if Path(value).is_absolute():
        raise ValueError(f"ignore path must be relative to tex/: {value}")
    path = Path(value)
    if any(part == ".." for part in path.parts):
        raise ValueError(f"ignore path must stay under tex/: {value}")
    if path.as_posix() in {"", "."}:
        raise ValueError("ignore path must be a non-empty path relative to tex/")
    return value
