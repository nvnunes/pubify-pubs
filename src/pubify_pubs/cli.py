from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import subprocess
import shlex
import sys
import threading
import time

from pubify_pubs.export import close_figure_export_sources
from pubify_pubs.config import add_sync_exclude, load_workspace_config
from pubify_pubs.discovery import (
    PublicationDefinition,
    PublicationPaths,
    build_publication_paths,
    find_workspace_root,
    list_publication_ids,
    load_publication_definition,
)
from pubify_pubs.latex_bootstrap import (
    build_figure_latex_spec,
    render_figure_latex,
    render_stat_latex,
    render_table_latex,
)
from pubify_pubs.mirror import diff_publication, merge_conflicting_file, pull_publication, push_publication
from pubify_pubs.pinning import pin_loader
from pubify_pubs.runtime import (
    RunContext,
    UserCodeExecutionError,
    build_run_context,
    build_pdf_path,
    build_publication,
    clear_autofigures,
    check_publication,
    check_tables,
    clear_publication_build,
    generated_outputs_are_stale,
    init_publication,
    init_publication_by_id,
    inspect_figure,
    preload_loaders,
    resolve_loader,
    run_figures,
    run_stats,
    run_tables,
    update_stats,
    write_computed_stats,
    write_computed_tables,
)
from pubify_pubs.stats import ComputedStat
from pubify_pubs.stubs import (
    add_stub_to_figures_module,
    generated_stub_function_name,
    module_function_names,
    validate_stub_id,
)
from pubify_pubs.versioning import (
    PublicationVersion,
    build_publication_version_diff,
    create_publication_version,
    list_publication_versions,
    undo_publication_version_create,
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
ANSI_BOLD = "\033[1m"
ANSI_WHITE = "\033[97m"
ANSI_YELLOW = "\033[33m"
ANSI_BLUE = "\033[34m"
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
class StatInventoryRow:
    stat_id: str


@dataclass(frozen=True)
class TableInventoryRow:
    table_id: str


@dataclass(frozen=True)
class PublicationCommand:
    command: str
    arg3: str | None = None
    arg4: str | None = None
    arg5: str | None = None
    force: bool = False
    update_before_build: bool = False
    skip_update: bool = False
    clear_build: bool = False


class _ReportedExecutionError(RuntimeError):
    """Raised after a dynamic execution failure has already been rendered."""


class _LiveSectionPrinter:
    _ANIMATION_SUFFIXES = (".", "..", "...")
    _ANIMATION_INTERVAL_S = 1.0

    def __init__(self, title: str, *, use_color: bool, live: bool | None = None) -> None:
        self.title = title
        self.use_color = use_color
        self.live = sys.stdout.isatty() if live is None else live
        self.started = False
        self.active = False
        self._active_label: str | None = None
        self._active_action: str | None = None
        self._stop_event: threading.Event | None = None
        self._animation_thread: threading.Thread | None = None

    def ensure_heading(self) -> None:
        if self.started:
            return
        print(_render_section_heading(self.title, use_color=self.use_color))
        self.started = True

    def start_item(self, label: str, action: str) -> None:
        self.ensure_heading()
        if not self.live:
            return
        self._stop_animation()
        self._active_label = label
        self._active_action = action
        print(_render_execution_status_line(label, f"{action}...", use_color=self.use_color, state="pending"))
        self.active = True
        self._start_animation()

    def succeed(self, label: str, *, detail_lines: list[str] | None = None) -> None:
        self.ensure_heading()
        self._finish(
            _render_execution_status_line(
                label,
                self._success_word(),
                use_color=self.use_color,
                state="success",
            )
        )
        self._print_detail_lines(detail_lines or [])

    def fail(self, label: str, *, detail_lines: list[str] | None = None) -> None:
        self.ensure_heading()
        self._finish(
            _render_execution_status_line(label, "failed", use_color=self.use_color, state="failure")
        )
        self._print_detail_lines(detail_lines or [])

    def close(self) -> None:
        self._stop_animation()
        if self.started:
            print()

    def _finish(self, line: str) -> None:
        self._stop_animation()
        if self.live and self.active:
            sys.stdout.write("\033[1A\r\033[2K")
            sys.stdout.write(f"{line}\n")
            sys.stdout.flush()
            self.active = False
            self._active_label = None
            self._active_action = None
            return
        print(line)
        self._active_label = None
        self._active_action = None

    def _print_detail_lines(self, lines: list[str]) -> None:
        for line in lines:
            print(_render_detail_line(f"  {line}" if line else "", use_color=self.use_color))

    def _success_word(self) -> str:
        return "loaded" if self.title == "Data" else "updated"

    def _start_animation(self) -> None:
        if not self.live or not self.active or self._active_label is None or self._active_action is None:
            return
        self._stop_event = threading.Event()
        self._animation_thread = threading.Thread(target=self._animate_active_line, daemon=True)
        self._animation_thread.start()

    def _stop_animation(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._animation_thread is not None:
            self._animation_thread.join(timeout=0.5)
        self._stop_event = None
        self._animation_thread = None

    def _animate_active_line(self) -> None:
        if self._stop_event is None or self._active_label is None or self._active_action is None:
            return
        stop_event = self._stop_event
        label = self._active_label
        action = self._active_action
        suffix_index = len(self._ANIMATION_SUFFIXES) - 1
        while not stop_event.wait(self._ANIMATION_INTERVAL_S):
            suffix = self._ANIMATION_SUFFIXES[suffix_index % len(self._ANIMATION_SUFFIXES)]
            suffix_index += 1
            line = _render_execution_status_line(
                label,
                f"{action}{suffix}",
                use_color=self.use_color,
                state="pending",
            )
            sys.stdout.write("\033[1A\r\033[2K")
            sys.stdout.write(f"{line}\n")
            sys.stdout.flush()


@dataclass
class PublicationShellSession:
    workspace_root: Path
    publication_id: str
    publication: PublicationDefinition
    fingerprints: dict[Path, float | None]
    loader_cache: dict[str, object]
    pending_data_output: dict[str, list[str]]
    build_seen: bool = False


def build_parser() -> argparse.ArgumentParser:
    """Build the ``pubs`` CLI parser for workspace and publication commands."""

    parser = argparse.ArgumentParser(
        prog="pubs",
        usage="pubs [--force] [--update] [--skipupdate] [--clear] <command>",
        description=(
            "Commands:\n"
            "  pubs list\n"
            "  pubs init <publication-id>\n"
            "\n"
            "  pubs <publication-id> prepare\n"
            "  pubs <publication-id> check\n"
            "  pubs <publication-id> update\n"
            "  pubs <publication-id> shell\n"
            "  pubs <publication-id> data [list|add <data-id>]\n"
            "  pubs <publication-id> data <loader-id> pin\n"
            "  pubs <publication-id> figure [list|add <figure-id>|update|<figure-id> update|<figure-id> preview [<subfig-idx>]|<figure-id> latex [subcaption]]\n"
            "  pubs <publication-id> stat [list|add <stat-id>|update|<stat-id> update|<stat-id> latex]\n"
            "  pubs <publication-id> table [list|add <table-id>|update|check|<table-id> update|<table-id> check|<table-id> latex]\n"
            "  pubs <publication-id> version [list|create [note]|diff <version-id> [<version-id>]]\n"
            "  pubs <publication-id> ignore <relative-path>\n"
            "  pubs <publication-id> build [--update|--skipupdate] [--clear]\n"
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
    parser.add_argument("--update", dest="update_before_build", action="store_true")
    parser.add_argument("--skipupdate", action="store_true")
    parser.add_argument("--clear", dest="clear_build", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the ``pubs`` CLI and return its process exit code."""

    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.subject == "list":
            workspace_root = find_workspace_root()
            _reject_build_flags(parser, "list", args.update_before_build, args.skipupdate, args.clear_build)
            if any(value is not None for value in (args.arg2, args.arg3, args.arg4, args.arg5)):
                parser.error("list does not accept additional arguments")
            for publication_id in list_publication_ids(workspace_root):
                print(publication_id)
            return 0

        if args.subject == "init":
            workspace_root = find_workspace_root()
            _reject_build_flags(parser, "init", args.update_before_build, args.skipupdate, args.clear_build)
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
        if command == "stats":
            command = "stat"
        if command == "tables":
            command = "table"
        if command == "versions":
            command = "version"
        if command == "init":
            parser.error("use 'pubs init <publication-id>'")
        if command not in {
            "prepare",
            "check",
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
            _reject_build_flags(parser, "shell", args.update_before_build, args.skipupdate, args.clear_build)
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
            update_before_build=args.update_before_build,
            skip_update=args.skipupdate,
            clear_build=args.clear_build,
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
    except UserCodeExecutionError as exc:
        _print_indented_lines(exc.lines, stream=sys.stderr)
        return 1
    except _ReportedExecutionError:
        return 1
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
    update_before_build: bool,
    skip_update: bool,
    clear_build: bool,
) -> None:
    if update_before_build or skip_update or clear_build:
        parser.error(f"{command} does not accept --update, --skipupdate, or --clear")


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


def _add_publication_stub(publication: PublicationDefinition, *, kind: str, stub_id: str) -> None:
    validate_stub_id(stub_id)
    if kind == "data" and stub_id in publication.loaders:
        raise ValueError(f"Loader '{stub_id}' already exists")
    if kind == "figure" and stub_id in publication.figures:
        raise ValueError(f"Figure '{stub_id}' already exists")
    if kind == "stat" and stub_id in publication.stats:
        raise ValueError(f"Stat '{stub_id}' already exists")
    if kind == "table" and stub_id in publication.tables:
        raise ValueError(f"Table '{stub_id}' already exists")

    function_name = generated_stub_function_name(kind, stub_id)
    if function_name in module_function_names(publication.paths.entrypoint):
        raise ValueError(
            f"Function '{function_name}' already exists in {publication.paths.entrypoint}"
        )

    add_stub_to_figures_module(publication.paths.entrypoint, kind=kind, stub_id=stub_id)


def _run_publication_command(
    publication: PublicationDefinition,
    command: PublicationCommand,
    *,
    error: Callable[[str], None],
    use_color: bool,
    use_interactive_merge: bool,
    loader_cache: dict[str, object] | None = None,
    pending_data_output: dict[str, list[str]] | None = None,
    pending_data_loader_ids: tuple[str, ...] | None = None,
    force_build_refresh: bool = False,
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

    if command.command == "prepare":
        _reject_build_flags_from_command(command, error)
        if command.force:
            error("prepare does not accept --force")
        if command.arg3 is not None or command.arg4 is not None or command.arg5 is not None:
            error("prepare does not accept additional arguments")
        changed_paths = _refresh_publication_support(publication)
        _print_updated_publication_files(publication, changed_paths, use_color=use_color)
        print(f"{publication.publication_id}: prepared")
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

    if command.command == "update":
        _reject_build_flags_from_command(command, error)
        if command.force:
            error("update does not accept --force")
        if command.arg3 is not None or command.arg4 is not None or command.arg5 is not None:
            error("update does not accept additional arguments")
        changed_paths = _refresh_publication_support(publication)
        ctx = _command_run_context(
            publication,
            loader_cache=loader_cache,
            pending_data_output=pending_data_output,
        )
        if pending_data_loader_ids is not None:
            ctx.updated_loader_ids.update(pending_data_loader_ids)
        loader_ids = _build_refresh_loader_ids(publication)
        _run_data_updates(ctx, loader_ids, use_color=use_color, include_nocache=True)
        _print_updated_publication_files(publication, changed_paths, use_color=use_color)
        _print_update_outputs(publication, ctx, use_color=use_color)
        return 0

    if command.command == "data":
        _reject_build_flags_from_command(command, error)
        if command.force:
            error("data does not accept --force")
        if command.arg3 in {None, "list"}:
            if command.arg4 is not None or command.arg5 is not None:
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
        if command.arg3 == "add":
            if command.arg4 is None:
                error("data add requires <data-id>")
            if command.arg5 is not None:
                error("data add accepts only <data-id>")
            _add_publication_stub(publication, kind="data", stub_id=command.arg4)
            _print_added_stub("Data", command.arg4, use_color=use_color)
            return 0
        if command.arg3 is None or command.arg4 != "pin":
            error("data supports only 'list', 'add <data-id>', or '<loader-id> pin'")
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
        if command.arg3 == "add":
            if command.arg4 is None:
                error("figure add requires <figure-id>")
            if command.arg5 is not None:
                error("figure add accepts only <figure-id>")
            _add_publication_stub(publication, kind="figure", stub_id=command.arg4)
            _print_added_stub("Figures", command.arg4, use_color=use_color)
            return 0
        if command.arg3 == "update":
            if command.arg4 is not None or command.arg5 is not None:
                error("figure update does not accept additional arguments")
            ctx = _command_run_context(
                publication,
                loader_cache=loader_cache,
                pending_data_output=pending_data_output,
            )
            figure_ids = _selected_figure_ids(publication)
            loader_ids = _figure_loader_ids(publication)
            _run_data_updates(ctx, loader_ids, use_color=use_color, include_nocache=True)
            _run_figure_updates(publication, ctx, figure_ids, use_color=use_color)
            return 0
        if command.arg4 == "update":
            if command.arg5 is not None:
                error("figure <figure-id> update does not accept additional arguments")
            ctx = _command_run_context(
                publication,
                loader_cache=loader_cache,
                pending_data_output=pending_data_output,
            )
            figure_ids = _selected_figure_ids(publication, command.arg3)
            loader_ids = _figure_loader_ids(publication, command.arg3)
            _run_data_updates(ctx, loader_ids, use_color=use_color, include_nocache=True)
            _run_figure_updates(publication, ctx, figure_ids, use_color=use_color)
            return 0
        if _is_latex_alias(command.arg4):
            selected_id = command.arg3
            if selected_id not in publication.figures:
                raise KeyError(f"Unknown figure '{selected_id}'")
            if command.arg5 not in {None, "subcaption"}:
                error("figure <figure-id> latex accepts only optional 'subcaption'")
            ctx = _command_run_context(publication, loader_cache=loader_cache)
            export = inspect_figure(publication, selected_id, ctx=ctx)
            try:
                snippet = render_figure_latex(
                    build_figure_latex_spec(selected_id, export),
                    subcaption=command.arg5 == "subcaption",
                )
            finally:
                close_figure_export_sources(export)
            _print_emitted_latex(_with_main_tex_prelude(publication, "figure", snippet))
            return 0
        if command.arg4 != "preview":
            error(
                "figure supports only 'list', 'add <figure-id>', 'update', '<figure-id> update', "
                "'<figure-id> preview [<subfig-idx>]', or '<figure-id> latex [subcaption]'"
            )
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

    if command.command == "stat":
        _reject_build_flags_from_command(command, error)
        if command.force:
            error("stat does not accept --force")
        if command.arg3 in {None, "list"}:
            if command.arg4 is not None or command.arg5 is not None:
                error("stat list does not accept additional arguments")
            rows = _build_stat_inventory_rows(publication)
            if not rows:
                print(f"{publication.publication_id}: no stats")
                return 0
            for row in rows:
                print(row.stat_id)
            return 0
        if command.arg3 == "add":
            if command.arg4 is None:
                error("stat add requires <stat-id>")
            if command.arg5 is not None:
                error("stat add accepts only <stat-id>")
            _add_publication_stub(publication, kind="stat", stub_id=command.arg4)
            _print_added_stub("Stats", command.arg4, use_color=use_color)
            return 0
        if command.arg3 == "update":
            if command.arg4 is not None or command.arg5 is not None:
                error("stat update does not accept additional arguments")
            ctx = _command_run_context(
                publication,
                loader_cache=loader_cache,
                pending_data_output=pending_data_output,
            )
            loader_ids = _stat_loader_ids(publication)
            _run_data_updates(ctx, loader_ids, use_color=use_color, include_nocache=True)
            _run_stat_updates(publication, ctx, tuple(sorted(publication.stats)), use_color=use_color)
            return 0
        if _is_latex_alias(command.arg4):
            if command.arg5 is not None:
                error("stat <stat-id> latex does not accept additional arguments")
            selected_id = command.arg3
            if selected_id not in publication.stats:
                raise KeyError(f"Unknown stat '{selected_id}'")
            ctx = _command_run_context(publication, loader_cache=loader_cache)
            _print_emitted_latex(
                _with_main_tex_prelude(
                    publication,
                    "stat",
                    render_stat_latex(run_stats(publication, selected_id, ctx=ctx)[0]),
                )
            )
            return 0
        if command.arg4 != "update" or command.arg5 is not None:
            error("stat supports only 'list', 'add <stat-id>', 'update', '<stat-id> update', or '<stat-id> latex'")
        selected_id = command.arg3
        if selected_id not in publication.stats:
            raise KeyError(f"Unknown stat '{selected_id}'")
        ctx = _command_run_context(
            publication,
            loader_cache=loader_cache,
            pending_data_output=pending_data_output,
        )
        loader_ids = _stat_loader_ids(publication)
        _run_data_updates(ctx, loader_ids, use_color=use_color, include_nocache=True)
        _run_stat_updates(publication, ctx, (selected_id,), use_color=use_color)
        return 0

    if command.command == "table":
        _reject_build_flags_from_command(command, error)
        if command.force:
            error("table does not accept --force")
        if command.arg3 in {None, "list"}:
            if command.arg4 is not None or command.arg5 is not None:
                error("table list does not accept additional arguments")
            rows = _build_table_inventory_rows(publication)
            if not rows:
                print(f"{publication.publication_id}: no tables")
                return 0
            for row in rows:
                print(row.table_id)
            return 0
        if command.arg3 == "add":
            if command.arg4 is None:
                error("table add requires <table-id>")
            if command.arg5 is not None:
                error("table add accepts only <table-id>")
            _add_publication_stub(publication, kind="table", stub_id=command.arg4)
            _print_added_stub("Tables", command.arg4, use_color=use_color)
            return 0
        if command.arg3 == "update":
            if command.arg4 is not None or command.arg5 is not None:
                error("table update does not accept additional arguments")
            ctx = _command_run_context(
                publication,
                loader_cache=loader_cache,
                pending_data_output=pending_data_output,
            )
            loader_ids = _table_loader_ids(publication)
            _run_data_updates(ctx, loader_ids, use_color=use_color, include_nocache=True)
            _run_table_updates(publication, ctx, tuple(sorted(publication.tables)), use_color=use_color)
            return 0
        if command.arg3 == "check":
            if command.arg4 is not None or command.arg5 is not None:
                error("table check does not accept additional arguments")
            check_tables(publication)
            print(f"{publication.publication_id}: ok")
            return 0
        if _is_latex_alias(command.arg4):
            if command.arg5 is not None:
                error("table <table-id> latex does not accept additional arguments")
            selected_id = command.arg3
            if selected_id not in publication.tables:
                raise KeyError(f"Unknown table '{selected_id}'")
            ctx = _command_run_context(publication, loader_cache=loader_cache)
            _print_emitted_latex(
                _with_main_tex_prelude(
                    publication,
                    "table",
                    render_table_latex(run_tables(publication, selected_id, ctx=ctx)[0]),
                )
            )
            return 0
        if command.arg4 == "update":
            if command.arg5 is not None:
                error("table <table-id> update does not accept additional arguments")
            selected_id = command.arg3
            if selected_id not in publication.tables:
                raise KeyError(f"Unknown table '{selected_id}'")
            ctx = _command_run_context(
                publication,
                loader_cache=loader_cache,
                pending_data_output=pending_data_output,
            )
            loader_ids = _table_loader_ids(publication, selected_id)
            _run_data_updates(ctx, loader_ids, use_color=use_color, include_nocache=True)
            _run_table_updates(publication, ctx, (selected_id,), use_color=use_color)
            return 0
        if command.arg4 == "check" and command.arg5 is None:
            selected_id = command.arg3
            if selected_id not in publication.tables:
                raise KeyError(f"Unknown table '{selected_id}'")
            check_tables(publication, selected_id)
            print(f"{selected_id}: ok")
            return 0
        error(
            "table supports only 'list', 'add <table-id>', 'update', 'check', '<table-id> update', "
            "'<table-id> check', or '<table-id> latex'"
        )

    if command.command == "version":
        _reject_build_flags_from_command(command, error)
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
                print(_render_version_line(version))
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
            version = create_publication_version(
                publication,
                note=command.arg4 or "",
            )
            print(_render_version_line(version))
            return 0
        if command.arg3 == "diff":
            if command.arg4 is None:
                error("version diff requires <version-id> [<version-id>]")
            pdf_path = build_publication_version_diff(
                publication,
                command.arg4,
                command.arg5,
            )
            print(pdf_path)
            return 0
        error("version supports only 'list', 'create [note|undo]', or 'diff <version-id> [<version-id>]'")

    if command.command == "build":
        if command.force:
            error("build does not accept --force")
        if command.update_before_build and command.skip_update:
            error("build accepts only one of --update or --skipupdate")
        if command.arg3 is not None or command.arg4 is not None or command.arg5 is not None:
            error("build does not accept additional arguments")
        if command.clear_build:
            clear_publication_build(publication)
        if not command.skip_update and (
            command.update_before_build
            or force_build_refresh
            or generated_outputs_are_stale(publication)
        ):
            changed_paths = _refresh_publication_support(publication)
            ctx = _command_run_context(
                publication,
                loader_cache=loader_cache,
                pending_data_output=pending_data_output,
            )
            if pending_data_loader_ids is not None:
                ctx.updated_loader_ids.update(pending_data_loader_ids)
            loader_ids = _build_refresh_loader_ids(publication)
            _run_data_updates(ctx, loader_ids, use_color=use_color, include_nocache=True)
            _print_updated_publication_files(publication, changed_paths, use_color=use_color)
            _print_update_outputs(publication, ctx, use_color=use_color)
        build_publication(publication)
        _print_updated_pdf(build_pdf_path(publication), use_color=use_color)
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
    loader_cache, pending_data_output = _preload_shell_loader_cache(publication)
    session = PublicationShellSession(
        workspace_root=workspace_root,
        publication_id=publication_id,
        publication=publication,
        fingerprints=_collect_reload_fingerprints(publication.paths),
        loader_cache=loader_cache,
        pending_data_output=pending_data_output,
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
            _run_data_updates(
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
                    update_before_build=parsed.update_before_build,
                    skip_update=parsed.skipupdate,
                    clear_build=parsed.clear_build,
                )
                if _is_add_stub_command(publication_command):
                    did_reload = False
                else:
                    did_reload = _reload_session_publication(
                        session,
                        force=(
                            publication_command.command == "update"
                            or (
                                publication_command.command == "build"
                                and publication_command.update_before_build
                            )
                        ),
                    )
                pending_data_loader_ids = (
                    tuple(sorted(session.publication.loaders))
                    if publication_command.command in {"update", "build"} and did_reload
                    else None
                )
                if publication_command.command in {"update", "build"}:
                    print()
                _run_publication_command(
                    session.publication,
                    publication_command,
                    error=_raise_value_error,
                    use_color=sys.stdout.isatty(),
                    use_interactive_merge=sys.stdout.isatty() and sys.stdin.isatty(),
                    loader_cache=session.loader_cache,
                    pending_data_output=session.pending_data_output,
                    pending_data_loader_ids=pending_data_loader_ids,
                    force_build_refresh=(
                        publication_command.command == "build"
                        and not publication_command.skip_update
                        and not session.build_seen
                    ),
                )
                if publication_command.command in {"build", "update"}:
                    session.build_seen = True
            except UserCodeExecutionError as exc:
                _print_indented_lines(exc.lines, stream=sys.stderr)
            except _ReportedExecutionError:
                continue
            except (FileNotFoundError, ImportError, IndexError, KeyError, RuntimeError, SyntaxError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
    finally:
        _save_shell_history(readline_module, history_path)


def _build_shell_command_parser() -> argparse.ArgumentParser:
    parser = _ShellArgumentParser(add_help=False)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--update", dest="update_before_build", action="store_true")
    parser.add_argument("--skipupdate", action="store_true")
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
) -> bool:
    current = _collect_reload_fingerprints(session.publication.paths)
    if not force and current == session.fingerprints:
        return False
    publication = load_publication_definition(session.workspace_root, session.publication_id)
    loader_cache, pending_data_output = _preload_shell_loader_cache(publication)
    session.publication = publication
    session.fingerprints = _collect_reload_fingerprints(publication.paths)
    session.loader_cache = loader_cache
    session.pending_data_output = pending_data_output
    session.build_seen = False
    return True


def _shell_help_text(publication_id: str) -> str:
    return "\n".join(
        [
            f"Shell commands for {publication_id}:",
            "  prepare",
            "  check",
            "  data [list|add <data-id>]",
            "  data <loader-id> pin",
            "  figure [list|add <figure-id>|update|<figure-id> update|<figure-id> preview [<subfig-idx>]|<figure-id> latex [subcaption]]",
            "  stat [list|add <stat-id>|update|<stat-id> update|<stat-id> latex]",
            "  table [list|add <table-id>|update|check|<table-id> update|<table-id> check|<table-id> latex]",
            "  version [list|create [note]|diff <version-id> [<version-id>]]",
            "  update",
            "  ignore <relative-path>",
            "  build [--update|--skipupdate] [--clear]",
            "  preview",
            "  push [--force]",
            "  pull [--force]",
            "  diff [list|<relative-path>]",
            "  help",
            "  quit",
        ]
    )


class _ShellArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _is_add_stub_command(command: PublicationCommand) -> bool:
    return command.command in {"data", "figure", "stat", "table"} and command.arg3 == "add"


def _is_latex_alias(value: str | None) -> bool:
    return value in {"latex", "tex"}


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
    if command.update_before_build or command.skip_update or command.clear_build:
        error(f"{command.command} does not accept --update, --skipupdate, or --clear")


def _build_stat_inventory_rows(publication: PublicationDefinition) -> list[StatInventoryRow]:
    return [StatInventoryRow(stat_id=stat_id) for stat_id in sorted(publication.stats)]


def _build_table_inventory_rows(publication: PublicationDefinition) -> list[TableInventoryRow]:
    return [TableInventoryRow(table_id=table_id) for table_id in sorted(publication.tables)]


def _print_update_outputs(
    publication: PublicationDefinition,
    ctx: RunContext,
    *,
    use_color: bool,
) -> None:
    if publication.figures:
        _run_figure_updates(publication, ctx, _selected_figure_ids(publication), use_color=use_color)
    if publication.stats:
        _run_stat_updates(publication, ctx, tuple(sorted(publication.stats)), use_color=use_color)
    if publication.tables:
        _run_table_updates(publication, ctx, tuple(sorted(publication.tables)), use_color=use_color)


def _refresh_publication_support(publication: PublicationDefinition) -> tuple[Path, ...]:
    before_contents: dict[Path, bytes | None] = {}
    support_paths = (
        publication.paths.tex_root / "pubify.sty",
        publication.paths.tex_root / "pubify-template.tex",
    )
    for path in support_paths:
        before_contents[path] = path.read_bytes() if path.exists() else None

    changed_paths: list[Path] = []
    prepared_paths = init_publication(publication)
    for path in prepared_paths:
        if _path_content_changed(path, before_contents.get(path)):
            changed_paths.append(path)
    return tuple(changed_paths)


def _path_content_changed(path: Path, previous_content: bytes | None) -> bool:
    current_content = path.read_bytes() if path.exists() else None
    return previous_content != current_content


def _print_updated_publication_files(
    publication: PublicationDefinition,
    changed_paths: tuple[Path, ...],
    *,
    use_color: bool,
) -> None:
    if not changed_paths:
        return
    print(_render_section_heading("Publication Files", use_color=use_color))
    for path in changed_paths:
        try:
            display = path.relative_to(publication.paths.publication_root)
        except ValueError:
            display = path
        print(_render_execution_status_line(str(display), "updated", use_color=use_color, state="success"))
    print()


def _print_added_stub(section: str, stub_id: str, *, use_color: bool) -> None:
    print(_render_section_heading(section, use_color=use_color))
    print(_render_execution_status_line(stub_id, "added", use_color=use_color, state="success"))
    print()


def _render_version_line(version: PublicationVersion) -> str:
    try:
        formatted_created_at = datetime.fromisoformat(version.created_at).strftime("%Y-%m-%d %I:%M %p")
    except ValueError:
        formatted_created_at = version.created_at
    if version.note:
        return f"{version.version_id}  {formatted_created_at}  {version.note}"
    return f"{version.version_id}  {formatted_created_at}"


def _print_emitted_latex(snippet: str) -> None:
    print()
    print(snippet)
    print()


def _with_main_tex_prelude(
    publication: PublicationDefinition,
    kind: str,
    snippet: str,
) -> str:
    prelude_lines = _missing_latex_prelude_lines(publication, kind)
    if not prelude_lines:
        return snippet
    return "\n".join([*prelude_lines, snippet])


def _missing_latex_prelude_lines(publication: PublicationDefinition, kind: str) -> list[str]:
    main_tex_text = _read_main_tex_text(publication)
    missing: list[str] = []
    if kind == "figure" and not _main_tex_has_pubify_package(main_tex_text):
        missing.append(r"\usepackage{pubify}")
    if kind == "stat" and r"\input{autostats.tex}" not in main_tex_text:
        missing.append(r"\input{autostats.tex}")
    if kind == "table" and r"\input{autotables.tex}" not in main_tex_text:
        missing.append(r"\input{autotables.tex}")
    return missing


def _read_main_tex_text(publication: PublicationDefinition) -> str:
    main_tex_path = publication.paths.tex_root / publication.config.main_tex_path
    return main_tex_path.read_text(encoding="utf-8")


def _main_tex_has_pubify_package(main_tex_text: str) -> bool:
    return re.search(r"\\usepackage(?:\[[^\]]*\])?\{pubify\}", main_tex_text) is not None


def _run_figure_updates(
    publication: PublicationDefinition,
    ctx: RunContext,
    figure_ids: tuple[str, ...],
    *,
    use_color: bool,
) -> None:
    if not figure_ids:
        return
    clear_autofigures(publication)
    printer = _LiveSectionPrinter("Figures", use_color=use_color)
    try:
        for figure_id in figure_ids:
            printer.start_item(figure_id, "updating")
            try:
                output_paths = run_figures(publication, figure_id, ctx=ctx)
            except UserCodeExecutionError as exc:
                printer.fail(figure_id, detail_lines=list(exc.lines))
                raise _ReportedExecutionError() from exc
            detail_lines = _consume_dynamic_output(ctx, "figure")
            label = figure_id
            count = _count_figure_outputs(figure_id, output_paths)
            if count > 1:
                label = f"{figure_id} ({count})"
            printer.succeed(label, detail_lines=detail_lines)
    finally:
        printer.close()


def _run_stat_updates(
    publication: PublicationDefinition,
    ctx: RunContext,
    stat_ids: tuple[str, ...],
    *,
    use_color: bool,
) -> None:
    if not stat_ids:
        return
    printer = _LiveSectionPrinter("Stats", use_color=use_color)
    computed_stats: list[ComputedStat] = []
    try:
        for stat_id in stat_ids:
            printer.start_item(stat_id, "updating")
            try:
                computed = run_stats(publication, stat_id, ctx=ctx)
            except UserCodeExecutionError as exc:
                printer.fail(stat_id, detail_lines=list(exc.lines))
                raise _ReportedExecutionError() from exc
            computed_stat = computed[0]
            computed_stats.append(computed_stat)
            detail_lines = _consume_dynamic_output(ctx, "stat")
            detail_lines.extend(
                [f"\\{value.macro_name} = {value.display}" for value in computed_stat.values]
            )
            printer.succeed(stat_id, detail_lines=detail_lines)
    finally:
        printer.close()
    write_computed_stats(publication, tuple(computed_stats))


def _run_table_updates(
    publication: PublicationDefinition,
    ctx: RunContext,
    table_ids: tuple[str, ...],
    *,
    use_color: bool,
) -> None:
    if not table_ids:
        return
    printer = _LiveSectionPrinter("Tables", use_color=use_color)
    computed_tables = []
    try:
        for table_id in table_ids:
            printer.start_item(table_id, "updating")
            try:
                computed = run_tables(publication, table_id, ctx=ctx)
            except UserCodeExecutionError as exc:
                printer.fail(table_id, detail_lines=list(exc.lines))
                raise _ReportedExecutionError() from exc
            computed_table = computed[0]
            computed_tables.append(computed_table)
            detail_lines = _consume_dynamic_output(ctx, "table")
            if len(computed_table.body_texts) > 1:
                detail_lines.append(f"{len(computed_table.body_texts)} bodies")
            printer.succeed(table_id, detail_lines=detail_lines)
    finally:
        printer.close()
    write_computed_tables(publication, tuple(computed_tables))


def _count_figure_outputs(figure_id: str, output_paths: list[Path]) -> int:
    count = 0
    for path in output_paths:
        stem = path.stem
        if stem == figure_id or stem.startswith(f"{figure_id}_"):
            count += 1
    return count


def _consume_dynamic_output(ctx: RunContext, group: str) -> list[str]:
    lines = list(ctx.captured_output[group])
    ctx.captured_output[group].clear()
    return lines


def _print_indented_lines(lines: Sequence[str], *, stream: object) -> None:
    use_color = stream is sys.stderr and sys.stderr.isatty()
    for line in lines:
        text = f"  {line}" if line else ""
        if use_color and text:
            text = f"{ANSI_WHITE}{text}{ANSI_RESET}"
        print(text, file=stream)


def _run_data_updates(
    ctx: RunContext,
    loader_ids: tuple[str, ...],
    *,
    heading: str = "Data",
    show_all: bool = False,
    include_nocache: bool,
    use_color: bool,
) -> None:
    visible_loader_ids = loader_ids if show_all else tuple(
        loader_id
        for loader_id in loader_ids
        if loader_id in ctx.updated_loader_ids or _loader_needs_execution(ctx, loader_id, include_nocache)
    )
    if not visible_loader_ids:
        return
    printer = _LiveSectionPrinter(heading, use_color=use_color)
    try:
        for loader_id in visible_loader_ids:
            if loader_id in ctx.updated_loader_ids:
                detail_lines = list(ctx.captured_data_output.pop(loader_id, []))
                printer.succeed(loader_id, detail_lines=detail_lines)
                ctx.updated_loader_ids.discard(loader_id)
                continue
            printer.start_item(loader_id, "loading")
            try:
                resolve_loader(ctx, loader_id)
            except UserCodeExecutionError as exc:
                printer.fail(loader_id, detail_lines=list(exc.lines))
                raise _ReportedExecutionError() from exc
            detail_lines = list(ctx.captured_data_output.pop(loader_id, []))
            printer.succeed(loader_id, detail_lines=detail_lines)
            ctx.updated_loader_ids.discard(loader_id)
    finally:
        printer.close()


def _loader_needs_execution(ctx: RunContext, loader_id: str, include_nocache: bool) -> bool:
    loader = ctx.publication.loaders[loader_id]
    if loader.nocache:
        return include_nocache and loader_id not in ctx.command_loader_cache
    return loader_id not in ctx.loader_cache


def _render_section_heading(text: str, *, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{ANSI_BOLD}{ANSI_BLUE}{text}{ANSI_RESET}"


def _render_detail_line(text: str, *, use_color: bool) -> str:
    if not use_color or not text:
        return text
    return f"{ANSI_WHITE}{text}{ANSI_RESET}"


def _print_updated_pdf(path: Path, *, use_color: bool) -> None:
    printer = _LiveSectionPrinter("PDF", use_color=use_color)
    try:
        printer.succeed(str(path))
    finally:
        printer.close()


def _render_execution_status_line(label: str, status: str, *, use_color: bool, state: str) -> str:
    prefix = f"- {label}: "
    if not use_color:
        return prefix + status
    colored_label = f"{ANSI_YELLOW}- {label}:{ANSI_RESET}"
    if state == "success":
        colored_status = f"{ANSI_BOLD}{STATUS_COLORS['pinned']}{status}{ANSI_RESET}"
    elif state == "failure":
        colored_status = f"{ANSI_BOLD}{STATUS_COLORS['conflicting']}{status}{ANSI_RESET}"
    else:
        colored_status = f"{ANSI_WHITE}{status}{ANSI_RESET}"
    return f"{colored_label} {colored_status}"


def _command_run_context(
    publication: PublicationDefinition,
    *,
    loader_cache: dict[str, object] | None = None,
    pending_data_output: dict[str, list[str]] | None = None,
) -> RunContext:
    ctx = build_run_context(
        publication,
        loader_cache=loader_cache.copy() if loader_cache is not None else None,
    )
    if pending_data_output is not None:
        ctx.captured_data_output = pending_data_output
    return ctx


def _preload_shell_loader_cache(publication: PublicationDefinition) -> tuple[dict[str, object], dict[str, list[str]]]:
    loader_cache: dict[str, object] = {}
    ctx = build_run_context(publication, loader_cache=loader_cache)
    preload_loaders(ctx, tuple(sorted(publication.loaders)), include_nocache=False)
    return loader_cache, {key: list(value) for key, value in ctx.captured_data_output.items()}


def _selected_figure_ids(
    publication: PublicationDefinition,
    figure_id: str | None = None,
) -> tuple[str, ...]:
    if figure_id is None:
        return tuple(sorted(publication.figures))
    if figure_id not in publication.figures:
        raise KeyError(f"Unknown figure '{figure_id}'")
    return (figure_id,)


def _figure_loader_ids(publication: PublicationDefinition, figure_id: str | None = None) -> tuple[str, ...]:
    figure_ids = _selected_figure_ids(publication, figure_id)
    loader_ids: set[str] = set()
    for current_id in figure_ids:
        loader_ids.update(publication.figures[current_id].dependency_ids)
    return tuple(sorted(loader_ids))


def _stat_loader_ids(publication: PublicationDefinition) -> tuple[str, ...]:
    loader_ids: set[str] = set()
    for stat in publication.stats.values():
        loader_ids.update(stat.dependency_ids)
    return tuple(sorted(loader_ids))


def _table_loader_ids(publication: PublicationDefinition, table_id: str | None = None) -> tuple[str, ...]:
    if table_id is None:
        tables = publication.tables.values()
    else:
        if table_id not in publication.tables:
            raise KeyError(f"Unknown table '{table_id}'")
        tables = (publication.tables[table_id],)
    loader_ids: set[str] = set()
    for table in tables:
        loader_ids.update(table.dependency_ids)
    return tuple(sorted(loader_ids))


def _build_refresh_loader_ids(publication: PublicationDefinition) -> tuple[str, ...]:
    loader_ids = set(_figure_loader_ids(publication))
    loader_ids.update(_stat_loader_ids(publication))
    loader_ids.update(_table_loader_ids(publication))
    return tuple(sorted(loader_ids))


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
        f"Exported figure PDF does not exist for '{figure_id}'. Run `pubs {publication.publication_id} figure {figure_id} update` first."
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
