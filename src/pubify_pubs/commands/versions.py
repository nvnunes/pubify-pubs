from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from pubify_pubs.discovery import PublicationDefinition
from pubify_pubs.versioning import (
    PublicationVersion,
    build_publication_version_diff,
    create_publication_version,
    list_publication_versions,
    undo_publication_version_create,
)

from pubify_pubs.commands.common import PublicationCommand, reject_build_flags_from_command


def handle_command(
    publication: PublicationDefinition,
    command: PublicationCommand,
    *,
    error: Callable[[str], None],
) -> int | None:
    if command.command != "version":
        return None
    reject_build_flags_from_command(command, error)
    if command.force:
        error("version does not accept --force")
    if command.arg3 in {None, "list"}:
        if command.arg4 is not None or command.arg5 is not None:
            error("version list does not accept additional arguments")
        versions = list_publication_versions(publication)
        if not versions:
            print(f"{publication.publication_id}: no versions")
            return 0
        for version in versions:
            print(render_version_line(version))
        return 0
    if command.arg3 == "create":
        if command.arg4 == "undo":
            if command.arg5 is not None:
                error("version create undo does not accept additional arguments")
            version = undo_publication_version_create(publication)
            print(f"{version.version_id}: removed")
            return 0
        if command.arg5 is not None:
            error("version create accepts at most one optional note")
        version = create_publication_version(publication, note=command.arg4 or "")
        print(render_version_line(version))
        return 0
    if command.arg3 == "diff":
        if command.arg4 is None:
            error("version diff requires <version-id> [<version-id>]")
        print(build_publication_version_diff(publication, command.arg4, command.arg5))
        return 0
    error("version supports only 'list', 'create [note|undo]', or 'diff <version-id> [<version-id>]'")


def render_version_line(version: PublicationVersion) -> str:
    try:
        formatted_created_at = datetime.fromisoformat(version.created_at).strftime("%Y-%m-%d %I:%M %p")
    except ValueError:
        formatted_created_at = version.created_at
    if version.note:
        return f"{version.version_id}  {formatted_created_at}  {version.note}"
    return f"{version.version_id}  {formatted_created_at}"
