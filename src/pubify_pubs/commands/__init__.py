from __future__ import annotations

from collections.abc import Callable

from pubify_pubs.discovery import PublicationDefinition

from pubify_pubs.commands.common import PublicationCommand, PublicationShellSession
from pubify_pubs.commands import core, pinning, sync


def run_publication_command(
    publication: PublicationDefinition,
    command: PublicationCommand,
    *,
    error: Callable[[str], None],
    use_color: bool,
    use_interactive_merge: bool,
    loader_cache: dict[str, object] | None = None,
    pending_data_output: dict[str, list[str]] | None = None,
    shell_session: PublicationShellSession | None = None,
) -> int:
    result = pinning.handle_command(publication, command, error=error)
    if result is not None:
        return result
    result = core.handle_command(
        publication,
        command,
        error=error,
        use_color=use_color,
        loader_cache=loader_cache,
        pending_data_output=pending_data_output,
        shell_session=shell_session,
    )
    if result is not None:
        return result
    result = sync.handle_command(
        publication,
        command,
        error=error,
        use_color=use_color,
        use_interactive_merge=use_interactive_merge,
    )
    if result is not None:
        return result
    error(f"unsupported command '{command.command}'")
    return 2
