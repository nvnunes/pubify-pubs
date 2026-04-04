from __future__ import annotations

from collections.abc import Callable

from pubify_pubs.discovery import PublicationDefinition
from pubify_pubs.pinning import pin_loader

from pubify_pubs.commands.common import PublicationCommand, reject_build_flags_from_command


def handle_command(
    publication: PublicationDefinition,
    command: PublicationCommand,
    *,
    error: Callable[[str], None],
) -> int | None:
    if command.command != "data" or command.arg4 != "pin":
        return None
    reject_build_flags_from_command(command, error)
    if command.force:
        error("data does not accept --force")
    if command.arg3 is None or command.arg5 is not None:
        error("data supports only 'list', 'add <data-id>', or '<loader-id> pin'")
    result = pin_loader(publication, command.arg3)
    print(f"{publication.publication_id}: pinned loader {result.loader_id}")
    for path in result.copied_paths:
        print(path)
    print(result.decorator_summary)
    return 0
