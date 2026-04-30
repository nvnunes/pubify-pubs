from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shlex
import sys

from pubify_pubs.commands import run_publication_command as _dispatch_publication_command
from pubify_pubs.commands.common import (
    PublicationCommand,
    PublicationShellSession,
    ReportedExecutionError,
    SHELL_HISTORY_LIMIT,
    print_indented_lines,
)
from pubify_pubs.commands.core import is_add_stub_command, run_data_updates
from pubify_pubs.commands.registry import build_cli_description, build_shell_help_text
from pubify_pubs.config import WORKSPACE_CONFIG_FILENAME, load_workspace_config, write_default_workspace_config
from pubify_pubs.discovery import (
    PublicationDefinition,
    PublicationPaths,
    find_workspace_root,
    list_publication_ids,
    load_publication_definition,
)
from pubify_pubs.runtime import (
    UserCodeExecutionError,
    build_run_context,
    init_publication_by_id,
    preload_loaders,
)
from pubify_pubs.shell_incremental import (
    _current_figure_output_names,
    collect_shell_method_state,
    imported_module_fingerprints_changed,
    purge_modules_by_paths,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``pubs`` CLI parser for workspace and publication commands."""

    parser = argparse.ArgumentParser(
        prog="pubs",
        usage="pubs [--force] [--clear] <command>",
        description=build_cli_description(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("subject", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("arg2", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("arg3", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("arg4", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("arg5", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--clear", dest="clear_build", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the ``pubs`` CLI and return its process exit code."""

    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.subject == "list":
            workspace_root = find_workspace_root()
            _reject_build_flags(parser, "list", args.clear_build)
            if any(value is not None for value in (args.arg2, args.arg3, args.arg4, args.arg5)):
                parser.error("list does not accept additional arguments")
            for publication_id in list_publication_ids(workspace_root):
                print(publication_id)
            return 0

        if args.subject == "init":
            _reject_build_flags(parser, "init", args.clear_build)
            if args.arg3 is not None or args.arg4 is not None or args.arg5 is not None:
                parser.error("init accepts at most optional <publication-id>")
            if args.arg2 is None:
                if args.force:
                    parser.error("workspace init does not accept --force")
                workspace_root = _init_workspace(Path.cwd())
                print(workspace_root)
                return 0
            workspace_root = find_workspace_root()
            publication_root = init_publication_by_id(workspace_root, args.arg2, force=args.force)
            print(publication_root)
            return 0

        if args.subject is None or args.arg2 is None:
            _error_with_help(
                parser,
                "expected 'list', 'init', 'init <publication-id>', or '<publication-id> <command>'",
            )

        publication_id = args.subject
        command = args.arg2
        if command == "figures":
            command = "figure"
        if command == "stats":
            command = "stat"
        if command == "tables":
            command = "table"
        if command == "versions":
            command = "version"
        if command == "init":
            parser.error("use 'pubs init <publication-id>'")
        if command not in {
            "update",
            "shell",
            "data",
            "figure",
            "stat",
            "table",
            "version",
            "ignore",
            "build",
            "preview",
            "push",
            "pull",
            "diff",
        }:
            parser.error(f"unsupported command '{command}'")

        workspace_root = find_workspace_root()
        publication = load_publication_definition(workspace_root, publication_id)
        if command == "shell":
            _reject_build_flags(parser, "shell", args.clear_build)
            if args.force:
                parser.error("shell does not accept --force")
            if args.arg3 is not None or args.arg4 is not None or args.arg5 is not None:
                parser.error("shell does not accept additional arguments")
            return run_publication_shell(workspace_root, publication_id, publication)

        publication_command = PublicationCommand(
            command=command,
            arg3=args.arg3,
            arg4=args.arg4,
            arg5=args.arg5,
            force=args.force,
            clear_build=args.clear_build,
        )
        return _dispatch_publication_command(
            publication,
            publication_command,
            error=parser.error,
            use_color=sys.stdout.isatty(),
        )

        parser.error(f"Unsupported command: {command}")
        return 2
    except UserCodeExecutionError as exc:
        print_indented_lines(exc.lines, stream=sys.stderr)
        return 1
    except ReportedExecutionError:
        return 1
    except (FileNotFoundError, ImportError, IndexError, KeyError, RuntimeError, SyntaxError, ValueError) as exc:
        print(f"Error: {_rewrite_workspace_error_message(exc)}", file=sys.stderr)
        return 1


def _init_workspace(workspace_root: Path) -> Path:
    resolved_root = workspace_root.resolve()
    config_path = resolved_root / WORKSPACE_CONFIG_FILENAME
    if not config_path.exists():
        write_default_workspace_config(config_path)
    elif "data_root:" in config_path.read_text(encoding="utf-8"):
        print(
            "Warning: pubify-pubs.data_root is deprecated and ignored; use publication-local data/ symlinks instead.",
            file=sys.stderr,
        )
    workspace = load_workspace_config(resolved_root)
    workspace.publications_root.mkdir(parents=True, exist_ok=True)
    return resolved_root


def _rewrite_workspace_error_message(exc: Exception) -> str:
    message = str(exc)
    if isinstance(exc, FileNotFoundError):
        if message == "Could not locate workspace root from current working directory":
            return f"{message}. Run `pubs init` in your workspace root and try again."
        if message.startswith("Missing workspace config:"):
            return f"{message}. Run `pubs init` in your workspace root and try again."
    return message


def _error_with_help(parser: argparse.ArgumentParser, message: str) -> None:
    parser.print_help(sys.stderr)
    parser.exit(2, f"\n{parser.prog}: error: {message}\n")


def _raise_value_error(message: str) -> None:
    raise ValueError(message)


def _reject_build_flags(
    parser: argparse.ArgumentParser,
    command: str,
    clear_build: bool,
) -> None:
    if clear_build:
        parser.error(f"{command} does not accept --clear")


def run_publication_shell(
    workspace_root: Path,
    publication_id: str,
    publication: PublicationDefinition,
) -> int:
    readline_module = _configure_shell_readline()
    loader_cache, pending_data_output = _preload_shell_loader_cache(publication)
    method_state = collect_shell_method_state(publication)
    session = PublicationShellSession(
        workspace_root=workspace_root,
        publication_id=publication_id,
        publication=publication,
        fingerprints=_collect_reload_fingerprints(publication.paths, method_state.imported_module_paths),
        loader_cache=loader_cache,
        pending_data_output=pending_data_output,
        method_state=method_state,
        last_success_method_state=None,
        cached_figure_output_names={
            figure_id: _current_figure_output_names(publication, figure_id)
            for figure_id in publication.figures
        },
        cached_stats={},
        cached_tables={},
    )
    shell_parser = _build_shell_command_parser()
    history_path = _shell_history_path(publication.paths)
    _load_shell_history(readline_module, history_path)
    try:
        if publication.loaders:
            print()
            startup_ctx = build_run_context(publication, loader_cache=session.loader_cache)
            startup_ctx.captured_data_output = session.pending_data_output
            startup_ctx.updated_loader_ids = set(publication.loaders)
            run_data_updates(
                startup_ctx,
                tuple(sorted(publication.loaders)),
                heading="Data",
                show_all=True,
                include_nocache=False,
                use_color=sys.stdout.isatty(),
            )
            session.pending_data_output = startup_ctx.captured_data_output
        while True:
            try:
                line = input(f"{publication_id}> ")
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                print()
                continue

            if not line.strip():
                continue

            try:
                tokens = shlex.split(line)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                continue

            _remember_shell_history_entry(readline_module, history_path, line)

            command = tokens[0]
            if command in {"exit", "quit"}:
                return 0
            if command == "help":
                print(_shell_help_text(publication_id))
                continue
            if command in {"init", "list", "shell", "reload"}:
                print(f"Error: unsupported shell command '{command}'", file=sys.stderr)
                continue

            try:
                parsed = shell_parser.parse_args(tokens)
                parsed_command = parsed.command
                if parsed_command == "figures":
                    parsed_command = "figure"
                if parsed_command == "stats":
                    parsed_command = "stat"
                if parsed_command == "tables":
                    parsed_command = "table"
                if parsed_command == "versions":
                    parsed_command = "version"
                publication_command = PublicationCommand(
                    command=parsed_command,
                    arg3=parsed.arg3,
                    arg4=parsed.arg4,
                    arg5=parsed.arg5,
                    force=parsed.force,
                    clear_build=parsed.clear_build,
                )
                reload_result = _ReloadResult(False, False)
                if not is_add_stub_command(publication_command):
                    reload_result = _reload_session_publication(
                        session,
                        force=publication_command.command == "update",
                        purge_all_imported_modules=publication_command.command == "update",
                    )
                if publication_command.command in {"update", "build"}:
                    print()
                _dispatch_publication_command(
                    session.publication,
                    publication_command,
                    error=_raise_value_error,
                    use_color=sys.stdout.isatty(),
                    loader_cache=session.loader_cache,
                    pending_data_output=session.pending_data_output,
                    shell_session=session,
                )
            except UserCodeExecutionError as exc:
                print_indented_lines(exc.lines, stream=sys.stderr)
            except ReportedExecutionError:
                continue
            except (FileNotFoundError, ImportError, IndexError, KeyError, RuntimeError, SyntaxError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
    finally:
        _save_shell_history(readline_module, history_path)


def _build_shell_command_parser() -> argparse.ArgumentParser:
    parser = _ShellArgumentParser(add_help=False)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--clear", dest="clear_build", action="store_true")
    parser.add_argument("command")
    parser.add_argument("arg3", nargs="?")
    parser.add_argument("arg4", nargs="?")
    parser.add_argument("arg5", nargs="?")
    return parser


def _configure_shell_readline() -> object | None:
    try:
        import readline
    except ImportError:
        return None
    try:
        set_auto_history = getattr(readline, "set_auto_history", None)
        if callable(set_auto_history):
            set_auto_history(False)
    except Exception:
        pass
    try:
        readline.parse_and_bind("set editing-mode emacs")
    except Exception:
        pass

    doc = (readline.__doc__ or "").lower()
    if "libedit" in doc:
        bindings = (
            "bind ^I rl_complete",
            "bind ^[[A ed-prev-history",
            "bind ^[[B ed-next-history",
            "bind ^[OA ed-prev-history",
            "bind ^[OB ed-next-history",
            "bind ^[[C ed-next-char",
            "bind ^[[D ed-prev-char",
            "bind ^[OC ed-next-char",
            "bind ^[OD ed-prev-char",
        )
    else:
        bindings = (
            '"\\e[A": previous-history',
            '"\\e[B": next-history',
            '"\\eOA": previous-history',
            '"\\eOB": next-history',
            '"\\e[C": forward-char',
            '"\\e[D": backward-char',
            '"\\eOC": forward-char',
            '"\\eOD": backward-char',
        )

    for binding in bindings:
        try:
            readline.parse_and_bind(binding)
        except Exception:
            continue
    return readline


def _shell_history_path(paths: PublicationPaths) -> Path:
    return paths.publication_root / ".pubs-history"


def _load_shell_history(readline_module: object | None, history_path: Path) -> None:
    if readline_module is None or not history_path.exists():
        return
    try:
        readline_module.read_history_file(str(history_path))
    except Exception:
        return


def _save_shell_history(readline_module: object | None, history_path: Path) -> None:
    try:
        lines = _read_shell_history_file(history_path)[-SHELL_HISTORY_LIMIT:]
        history_path.write_text(
            "\n".join(lines) + ("\n" if lines else ""),
            encoding="utf-8",
        )
    except Exception:
        return


def _remember_shell_history_entry(
    readline_module: object | None,
    history_path: Path,
    line: str,
) -> None:
    try:
        history_lines = _read_shell_history_file(history_path)
        if history_lines and history_lines[-1] == line:
            return

        if readline_module is not None:
            history_length = readline_module.get_current_history_length()
            last_line = (
                readline_module.get_history_item(history_length)
                if history_length > 0
                else None
            )
            if last_line != line:
                readline_module.add_history(line)
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
    except Exception:
        return


def _read_shell_history_file(history_path: Path) -> list[str]:
    if not history_path.exists():
        return []
    return history_path.read_text(encoding="utf-8").splitlines()


def _collect_reload_fingerprints(
    paths: PublicationPaths,
    imported_module_paths: Sequence[Path] = (),
) -> dict[Path, float | None]:
    fingerprints: dict[Path, float | None] = {
        paths.entrypoint: _mtime_or_none(paths.entrypoint),
        paths.config_path: _mtime_or_none(paths.config_path),
    }
    helpers_py = paths.publication_root / "helpers.py"
    if helpers_py.exists():
        fingerprints[helpers_py] = helpers_py.stat().st_mtime
    helpers_pkg = paths.publication_root / "helpers"
    if helpers_pkg.is_dir():
        for helper_path in sorted(path for path in helpers_pkg.rglob("*") if path.is_file()):
            fingerprints[helper_path] = helper_path.stat().st_mtime
    for module_path in imported_module_paths:
        fingerprints[module_path] = _mtime_or_none(module_path)
    return fingerprints


@dataclass(frozen=True)
class _ReloadResult:
    reloaded: bool
    imported_modules_changed: bool = False


def _mtime_or_none(path: Path) -> float | None:
    if not path.exists():
        return None
    return path.stat().st_mtime


def _reload_session_publication(
    session: PublicationShellSession,
    *,
    force: bool = False,
    purge_all_imported_modules: bool = True,
) -> _ReloadResult:
    current = _collect_reload_fingerprints(session.publication.paths, session.method_state.imported_module_paths)
    imported_changed = imported_module_fingerprints_changed(session.method_state, current)
    if not force and current == session.fingerprints:
        return _ReloadResult(False, False)
    if purge_all_imported_modules:
        purge_modules_by_paths(session.method_state.imported_module_paths)
    else:
        changed_import_paths = [
            path
            for path in session.method_state.imported_module_paths
            if current.get(path) != session.method_state.imported_module_fingerprints.get(path)
        ]
        purge_modules_by_paths(changed_import_paths)
    publication = load_publication_definition(session.workspace_root, session.publication_id)
    session.publication = publication
    session.method_state = collect_shell_method_state(publication)
    session.fingerprints = _collect_reload_fingerprints(publication.paths, session.method_state.imported_module_paths)
    session.loader_cache = {}
    session.pending_data_output = {}
    return _ReloadResult(True, imported_changed)


def _shell_help_text(publication_id: str) -> str:
    return build_shell_help_text(publication_id)


class _ShellArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _preload_shell_loader_cache(publication: PublicationDefinition) -> tuple[dict[str, object], dict[str, list[str]]]:
    loader_cache: dict[str, object] = {}
    ctx = build_run_context(publication, loader_cache=loader_cache)
    preload_loaders(ctx, tuple(sorted(publication.loaders)), include_nocache=False)
    return loader_cache, {key: list(value) for key, value in ctx.captured_data_output.items()}


if __name__ == "__main__":
    raise SystemExit(main())
