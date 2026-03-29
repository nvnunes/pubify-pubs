from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import subprocess
import shlex
import sys

from pubify_pubs.config import add_sync_exclude, load_workspace_config
from pubify_pubs.discovery import (
    PublicationDefinition,
    PublicationPaths,
    build_publication_paths,
    find_workspace_root,
    list_publication_ids,
    load_publication_definition,
)
from pubify_pubs.mirror import diff_publication, merge_conflicting_file, pull_publication, push_publication
from pubify_pubs.pinning import pin_loader
from pubify_pubs.runtime import (
    build_pdf_path,
    build_publication,
    check_publication,
    generated_exports_are_stale,
    init_publication_by_id,
    run_figures,
)

STATUS_WIDTH = 14
STATUS_COLORS = {
    "conflicting": "\033[31m",
    "mirror-only": "\033[33m",
    "local-only": "\033[36m",
    "local-changed": "\033[36m",
    "mirror-changed": "\033[33m",
    "in-sync": "\033[32m",
    "unchanged": "\033[2m",
    "pinned": "\033[32m",
    "external": "\033[31m",
    "figure": "\033[34m",
}
ANSI_RESET = "\033[0m"
SHELL_HISTORY_LIMIT = 500


@dataclass(frozen=True)
class DataInventoryRow:
    status: str
    loader_id: str
    path: str


@dataclass(frozen=True)
class FigureInventoryRow:
    status: str
    figure_id: str
    dependencies: str


@dataclass(frozen=True)
class PublicationCommand:
    command: str
    arg3: str | None = None
    arg4: str | None = None
    arg5: str | None = None
    force: bool = False
    export_before_build: bool = False
    export_if_stale: bool = False


@dataclass
class PublicationShellSession:
    workspace_root: Path
    publication_id: str
    publication: PublicationDefinition
    fingerprints: dict[Path, float | None]


def build_parser() -> argparse.ArgumentParser:
    """Build the ``pubs`` CLI parser for workspace and publication commands."""

    parser = argparse.ArgumentParser(
        prog="pubs",
        usage="pubs [--force] [--export] [--export-if-stale] <command>",
        description=(
            "Commands:\n"
            "  pubs list\n"
            "  pubs init <publication-id>\n"
            "\n"
            "  pubs <publication-id> check\n"
            "  pubs <publication-id> shell\n"
            "  pubs <publication-id> figure [list|<figure-id> preview [<subfig-idx>]]\n"
            "  pubs <publication-id> export [<figure-id> [<subfig-idx>]]\n"
            "  pubs <publication-id> data [list]\n"
            "  pubs <publication-id> data <loader-id> pin\n"
            "  pubs <publication-id> ignore <relative-path>\n"
            "  pubs <publication-id> build [--export|--export-if-stale]\n"
            "  pubs <publication-id> preview\n"
            "  pubs <publication-id> push [--force]\n"
            "  pubs <publication-id> pull [--force]\n"
            "  pubs <publication-id> diff [list|<relative-path>]"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("subject", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("arg2", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("arg3", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("arg4", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("arg5", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--export", dest="export_before_build", action="store_true")
    parser.add_argument("--export-if-stale", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the ``pubs`` CLI and return its process exit code."""

    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.subject == "list":
            workspace_root = find_workspace_root()
            _reject_build_flags(parser, "list", args.export_before_build, args.export_if_stale)
            if any(value is not None for value in (args.arg2, args.arg3, args.arg4, args.arg5)):
                parser.error("list does not accept additional arguments")
            for publication_id in list_publication_ids(workspace_root):
                print(publication_id)
            return 0

        if args.subject == "init":
            workspace_root = find_workspace_root()
            _reject_build_flags(parser, "init", args.export_before_build, args.export_if_stale)
            if args.arg2 is None:
                parser.error("init requires <publication-id>")
            if args.arg3 is not None or args.arg4 is not None or args.arg5 is not None:
                parser.error("init accepts only <publication-id>")
            publication_root = init_publication_by_id(workspace_root, args.arg2)
            print(publication_root)
            return 0

        if args.subject is None or args.arg2 is None:
            _error_with_help(
                parser,
                "expected 'list', 'init <publication-id>', or '<publication-id> <command>'",
            )

        publication_id = args.subject
        command = args.arg2
        if command == "figures":
            command = "figure"
        if command == "init":
            parser.error("use 'pubs init <publication-id>'")
        if command not in {
            "check",
            "shell",
            "export",
            "data",
            "figure",
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
            _reject_build_flags(parser, "shell", args.export_before_build, args.export_if_stale)
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
            export_before_build=args.export_before_build,
            export_if_stale=args.export_if_stale,
        )
        return _run_publication_command(
            publication,
            publication_command,
            error=parser.error,
            use_color=sys.stdout.isatty(),
            use_interactive_merge=sys.stdout.isatty() and sys.stdin.isatty(),
        )

        parser.error(f"Unsupported command: {command}")
        return 2
    except (FileNotFoundError, ImportError, IndexError, KeyError, RuntimeError, SyntaxError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _parse_subfig_idx(parser: argparse.ArgumentParser, value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        parser.error(f"invalid <subfig-idx> '{value}'")
        raise exc


def _error_with_help(parser: argparse.ArgumentParser, message: str) -> None:
    parser.print_help(sys.stderr)
    parser.exit(2, f"\n{parser.prog}: error: {message}\n")


def _raise_value_error(message: str) -> None:
    raise ValueError(message)


def _reject_build_flags(
    parser: argparse.ArgumentParser,
    command: str,
    export_before_build: bool,
    export_if_stale: bool,
) -> None:
    if export_before_build or export_if_stale:
        parser.error(f"{command} does not accept --export or --export-if-stale")


def _parse_force_flag(
    parser: argparse.ArgumentParser,
    command: str,
    arg3: str | None,
    arg4: str | None,
    force_flag: bool,
) -> bool:
    values = [value for value in (arg3, arg4) if value is not None]
    if values:
        parser.error(f"{command} accepts only optional --force")
    if force_flag:
        return True
    return False


def _run_publication_command(
    publication: PublicationDefinition,
    command: PublicationCommand,
    *,
    error: Callable[[str], None],
    use_color: bool,
    use_interactive_merge: bool,
) -> int:
    if command.command == "ignore":
        _reject_build_flags_from_command(command, error)
        if command.force:
            error("ignore does not accept --force")
        if command.arg3 is None:
            error("ignore requires <relative-path>")
        if command.arg4 is not None:
            error("ignore accepts only <relative-path>")
        relative_path = _parse_ignore_path(command.arg3)
        config_path = publication.paths.config_path
        if not config_path.exists():
            raise FileNotFoundError(f"Missing publication config: {config_path}")
        added = add_sync_exclude(config_path, relative_path)
        if added:
            print(f"{publication.publication_id}: added sync ignore {relative_path}")
        else:
            print(f"{publication.publication_id}: sync ignore already present {relative_path}")
        return 0

    if command.command == "check":
        _reject_build_flags_from_command(command, error)
        if command.force:
            error("check does not accept --force")
        if command.arg3 is not None or command.arg4 is not None:
            error("check does not accept additional arguments")
        check_publication(publication)
        print(f"{publication.publication_id}: ok")
        return 0

    if command.command == "export":
        _reject_build_flags_from_command(command, error)
        if command.force:
            error("export does not accept --force")
        subfig_idx = None if command.arg4 is None else _parse_subfig_idx_value(command.arg4, error)
        for path in run_figures(publication, command.arg3, subfig_idx):
            print(path.relative_to(publication.paths.publication_root))
        return 0

    if command.command == "data":
        _reject_build_flags_from_command(command, error)
        if command.force:
            error("data does not accept --force")
        if command.arg3 in {None, "list"}:
            if command.arg4 is not None:
                error("data list does not accept additional arguments")
            rows = _build_data_inventory_rows(publication)
            if not rows:
                print(f"{publication.publication_id}: no declared data")
                return 0
            loader_width = max(len(row.loader_id) for row in rows)
            for row in rows:
                print(
                    _render_data_inventory_line(
                        row,
                        loader_width=loader_width,
                        use_color=use_color,
                    )
                )
            return 0
        if command.arg3 is None or command.arg4 != "pin":
            error("data supports only 'list' or '<loader-id> pin'")
        result = pin_loader(publication, command.arg3)
        print(f"{publication.publication_id}: pinned loader {result.loader_id}")
        for path in result.copied_paths:
            print(path)
            print(result.decorator_summary)
            return 0

    if command.command == "figure":
        _reject_build_flags_from_command(command, error)
        if command.force:
            error("figure does not accept --force")
        if command.arg3 in {None, "list"}:
            if command.arg4 is not None or command.arg5 is not None:
                error("figure list does not accept additional arguments")
            rows = _build_figure_inventory_rows(publication)
            if not rows:
                print(f"{publication.publication_id}: no figures")
                return 0
            figure_id_width = max(len(row.figure_id) for row in rows)
            for row in rows:
                print(
                    _render_figure_inventory_line(
                        row,
                        figure_id_width=figure_id_width,
                        use_color=use_color,
                    )
                )
            return 0
        if command.arg4 != "preview":
            error("figure supports only 'list' or '<figure-id> preview [<subfig-idx>]'")
        subfigure_index = None if command.arg5 is None else _parse_subfig_idx_value(command.arg5, error)
        preview_paths = _preview_figure_paths(
            publication,
            command.arg3,
            subfigure_index=subfigure_index,
        )
        workspace = load_workspace_config(publication.paths.workspace_root)
        _open_publication_previews(preview_paths, backend=workspace.preview.figure)
        for path in preview_paths:
            print(path.relative_to(publication.paths.publication_root))
        return 0

    if command.command == "build":
        if command.force:
            error("build does not accept --force")
        if command.export_before_build and command.export_if_stale:
            error("build accepts only one of --export or --export-if-stale")
        if command.arg3 is not None or command.arg4 is not None or command.arg5 is not None:
            error("build does not accept additional arguments")
        if command.export_before_build or (
            command.export_if_stale and generated_exports_are_stale(publication)
        ):
            for path in run_figures(publication):
                print(path.relative_to(publication.paths.publication_root))
        build_publication(publication)
        print(build_pdf_path(publication))
        return 0

    if command.command == "preview":
        _reject_build_flags_from_command(command, error)
        if command.force:
            error("preview does not accept --force")
        if command.arg3 is not None or command.arg4 is not None or command.arg5 is not None:
            error("preview does not accept additional arguments")
        pdf_path = build_pdf_path(publication)
        if not pdf_path.exists():
            raise FileNotFoundError(
                f"Built publication PDF does not exist: {pdf_path.resolve()}. "
                f"Run `pubs {publication.publication_id} build` first."
            )
        workspace = load_workspace_config(publication.paths.workspace_root)
        _open_publication_previews([pdf_path], backend=workspace.preview.publication)
        print(pdf_path)
        return 0

    if command.command == "push":
        _reject_build_flags_from_command(command, error)
        force = _parse_force_flag_value(command.command, command.arg3, command.arg4, command.force, error)
        result = push_publication(publication, force=force)
        for path in result.forced_paths:
            print(f"Forced overwrite: {path}", file=sys.stderr)
        print(f"{publication.publication_id}: pushed")
        return 0

    if command.command == "pull":
        _reject_build_flags_from_command(command, error)
        force = _parse_force_flag_value(command.command, command.arg3, command.arg4, command.force, error)
        result = pull_publication(publication, force=force)
        for warning in result.warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        for path in result.forced_paths:
            print(f"Forced overwrite: {path}", file=sys.stderr)
        print(f"{publication.publication_id}: pulled")
        return 0

    if command.command == "diff":
        _reject_build_flags_from_command(command, error)
        if command.force:
            error("diff does not accept --force")
        if command.arg4 is not None:
            error("diff accepts at most one optional selector")
        summary_only = command.arg3 == "list"
        rel_path = None if summary_only else command.arg3
        entries = diff_publication(publication, rel_path)
        for entry in entries:
            print(_render_status_line(entry.status, entry.path, use_color=use_color))
            if use_interactive_merge and rel_path is not None and entry.status == "conflicting":
                merge_conflicting_file(publication, entry.path)
                continue
            if not summary_only and entry.diff:
                print(entry.diff)
        return 0

    error(f"unsupported command '{command.command}'")
    return 2


def run_publication_shell(
    workspace_root: Path,
    publication_id: str,
    publication: PublicationDefinition,
) -> int:
    readline_module = _configure_shell_readline()
    session = PublicationShellSession(
        workspace_root=workspace_root,
        publication_id=publication_id,
        publication=publication,
        fingerprints=_collect_reload_fingerprints(publication.paths),
    )
    shell_parser = _build_shell_command_parser()
    history_path = _shell_history_path(publication.paths)
    _load_shell_history(readline_module, history_path)
    try:
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
            if command == "reload":
                try:
                    _reload_session_publication(session, force=True)
                except (FileNotFoundError, ImportError, IndexError, KeyError, RuntimeError, SyntaxError, ValueError) as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                else:
                    print(f"{publication_id}: reloaded")
                continue
            if command in {"init", "list", "shell"}:
                print(f"Error: unsupported shell command '{command}'", file=sys.stderr)
                continue

            try:
                parsed = shell_parser.parse_args(tokens)
                parsed_command = "figure" if parsed.command == "figures" else parsed.command
                publication_command = PublicationCommand(
                    command=parsed_command,
                    arg3=parsed.arg3,
                    arg4=parsed.arg4,
                    arg5=parsed.arg5,
                    force=parsed.force,
                    export_before_build=parsed.export_before_build,
                    export_if_stale=parsed.export_if_stale,
                )
                _reload_session_publication(session)
                _run_publication_command(
                    session.publication,
                    publication_command,
                    error=_raise_value_error,
                    use_color=sys.stdout.isatty(),
                    use_interactive_merge=sys.stdout.isatty() and sys.stdin.isatty(),
                )
            except (FileNotFoundError, ImportError, IndexError, KeyError, RuntimeError, SyntaxError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
    finally:
        _save_shell_history(readline_module, history_path)


def _build_shell_command_parser() -> argparse.ArgumentParser:
    parser = _ShellArgumentParser(add_help=False)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--export", dest="export_before_build", action="store_true")
    parser.add_argument("--export-if-stale", action="store_true")
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


def _shell_history_lines(readline_module: object) -> list[str]:
    history_length = readline_module.get_current_history_length()
    return [
        line
        for line in (
            readline_module.get_history_item(index)
            for index in range(1, history_length + 1)
        )
        if line is not None
    ]


def _read_shell_history_file(history_path: Path) -> list[str]:
    if not history_path.exists():
        return []
    return history_path.read_text(encoding="utf-8").splitlines()


def _collect_reload_fingerprints(paths: PublicationPaths) -> dict[Path, float | None]:
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
    return fingerprints


def _mtime_or_none(path: Path) -> float | None:
    if not path.exists():
        return None
    return path.stat().st_mtime


def _reload_session_publication(
    session: PublicationShellSession,
    *,
    force: bool = False,
) -> None:
    current = _collect_reload_fingerprints(session.publication.paths)
    if not force and current == session.fingerprints:
        return
    publication = load_publication_definition(session.workspace_root, session.publication_id)
    session.publication = publication
    session.fingerprints = _collect_reload_fingerprints(publication.paths)


def _shell_help_text(publication_id: str) -> str:
    return "\n".join(
        [
            f"Shell commands for {publication_id}:",
            "  check",
            "  figure [list|<figure-id> preview [<subfig-idx>]]",
            "  export [<figure-id> [<subfig-idx>]]",
            "  data [list]",
            "  data <loader-id> pin",
            "  ignore <relative-path>",
            "  build [--export|--export-if-stale]",
            "  preview",
            "  push [--force]",
            "  pull [--force]",
            "  diff [list|<relative-path>]",
            "  reload",
            "  help",
            "  quit",
        ]
    )


class _ShellArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _parse_subfig_idx_value(value: str, error: Callable[[str], None]) -> int:
    try:
        return int(value)
    except ValueError as exc:
        error(f"invalid <subfig-idx> '{value}'")
        raise exc


def _reject_build_flags_from_command(
    command: PublicationCommand,
    error: Callable[[str], None],
) -> None:
    if command.export_before_build or command.export_if_stale:
        error(f"{command.command} does not accept --export or --export-if-stale")


def _parse_force_flag_value(
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


def _parse_ignore_path(value: str) -> str:
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


def _open_publication_previews(paths: list[Path], *, backend: str) -> None:
    resolved_paths = [path.resolve() for path in paths]
    if not resolved_paths:
        raise ValueError("No preview paths were provided")
    for resolved_path in resolved_paths:
        if not resolved_path.exists():
            raise FileNotFoundError(f"Preview target does not exist: {resolved_path}")
    if backend == "preview":
        _open_with_preview(resolved_paths)
        return
    if backend == "vscode":
        _open_with_vscode(resolved_paths)
        return
    raise ValueError(f"Unsupported preview backend: {backend}")


def _open_with_preview(paths: list[Path]) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("The 'preview' backend is supported only on macOS")
    try:
        subprocess.run(
            ["open", "-a", "Preview", *(str(path) for path in paths)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Could not find the macOS `open` command") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        if stderr:
            raise RuntimeError(f"Preview failed: {stderr}") from None
        raise RuntimeError(f"Preview failed with exit code {exc.returncode}") from None


def _open_with_vscode(paths: list[Path]) -> None:
    try:
        subprocess.run(
            ["code", "-n", *(str(path) for path in paths)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Could not find the VS Code `code` command on PATH") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        if stderr:
            raise RuntimeError(f"VS Code preview failed: {stderr}") from None
        raise RuntimeError(f"VS Code preview failed with exit code {exc.returncode}") from None


def _preview_figure_paths(
    publication: PublicationDefinition,
    figure_id: str,
    *,
    subfigure_index: int | None = None,
) -> list[Path]:
    if figure_id not in publication.figures:
        raise KeyError(f"Unknown figure '{figure_id}'")
    root = publication.paths.autofigures_root
    primary = root / f"{figure_id}.pdf"
    if primary.exists():
        if subfigure_index is not None and subfigure_index != 1:
            raise IndexError(
                f"Figure '{figure_id}' has 1 panel(s); requested subfigure {subfigure_index}"
            )
        return [primary]
    paths = sorted(root.glob(f"{figure_id}_*.pdf"))
    if paths:
        if subfigure_index is not None:
            if subfigure_index < 1 or subfigure_index > len(paths):
                raise IndexError(
                    f"Figure '{figure_id}' has {len(paths)} panel(s); requested subfigure {subfigure_index}"
                )
            return [paths[subfigure_index - 1]]
        return paths
    raise FileNotFoundError(
        f"Exported figure PDF does not exist for '{figure_id}'. Run `pubs {publication.publication_id} export {figure_id}` first."
    )


def _render_status_line(status: str, path: str, *, use_color: bool) -> str:
    display_status = _display_status(status)
    return f"{_render_status_token(display_status, use_color=use_color)} {path}"


def _build_data_inventory_rows(publication: object) -> list[DataInventoryRow]:
    rows: list[DataInventoryRow] = []
    for loader in publication.loaders.values():
        for relative_path in loader.relative_paths.values():
            if loader.kind == "data":
                row = DataInventoryRow(
                    status="pinned",
                    loader_id=loader.loader_id,
                    path=relative_path,
                )
            elif loader.kind == "external_data":
                row = DataInventoryRow(
                    status="external",
                    loader_id=loader.loader_id,
                    path=_render_external_inventory_path(loader.root_name, relative_path),
                )
            else:
                continue
            rows.append(row)
    return rows


def _build_figure_inventory_rows(publication: object) -> list[FigureInventoryRow]:
    rows: list[FigureInventoryRow] = []
    for figure in publication.figures.values():
        rows.append(
            FigureInventoryRow(
                status="figure",
                figure_id=figure.figure_id,
                dependencies=", ".join(figure.dependency_ids),
            )
        )
    return rows


def _render_external_inventory_path(root_name: str | None, relative_path: str) -> str:
    if root_name is None:
        return relative_path
    return f"{root_name}:{relative_path}"


def _render_data_inventory_line(
    row: DataInventoryRow,
    *,
    loader_width: int,
    use_color: bool,
) -> str:
    status = _render_status_token_variant(row.status, use_color=use_color, padded=False)
    return f"{status}   {row.loader_id:<{loader_width}}   {row.path}"


def _render_figure_inventory_line(
    row: FigureInventoryRow,
    *,
    figure_id_width: int,
    use_color: bool,
) -> str:
    status = _render_status_token_variant(row.status, use_color=use_color, padded=False)
    return f"{status}   {row.figure_id:<{figure_id_width}}   {row.dependencies}"


def _render_status_token(status: str, *, use_color: bool) -> str:
    return _render_status_token_variant(status, use_color=use_color, padded=True)


def _display_status(status: str) -> str:
    if status == "in-sync":
        return "unchanged"
    return status


def _render_status_token_variant(status: str, *, use_color: bool, padded: bool) -> str:
    text = f"{status:<{STATUS_WIDTH}}" if padded else status
    if not use_color:
        return text
    color = STATUS_COLORS.get(status)
    if color is None:
        return text
    return f"{color}{text}{ANSI_RESET}"


if __name__ == "__main__":
    raise SystemExit(main())
