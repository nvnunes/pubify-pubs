from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import sys

from pubify_pubs.shell_incremental import ShellMethodState
from pubify_pubs.stats import ComputedStat
from pubify_pubs.tables import ComputedTable

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
    clear_build: bool = False


@dataclass
class PublicationShellSession:
    workspace_root: Path
    publication_id: str
    publication: object
    fingerprints: dict[Path, float | None]
    loader_cache: dict[str, object]
    pending_data_output: dict[str, list[str]]
    method_state: ShellMethodState
    last_success_method_state: ShellMethodState | None
    cached_figure_output_names: dict[str, tuple[str, ...]]
    cached_stats: dict[str, ComputedStat]
    cached_tables: dict[str, ComputedTable]


class ReportedExecutionError(RuntimeError):
    """Raised after a dynamic execution failure has already been rendered."""


class LiveSectionPrinter:
    def __init__(self, title: str, *, use_color: bool, live: bool | None = None) -> None:
        self.title = title
        self.use_color = use_color
        self.live = sys.stdout.isatty() if live is None else live
        self.started = False
        self.active = False
        self._active_label: str | None = None
        self._active_action: str | None = None

    def ensure_heading(self) -> None:
        if self.started:
            return
        print(render_section_heading(self.title, use_color=self.use_color))
        self.started = True

    def start_item(self, label: str, action: str) -> None:
        self.ensure_heading()
        if not self.live:
            return
        self._active_label = label
        self._active_action = action
        initial_status = f"{action}..."
        line = render_execution_status_line(
            label,
            initial_status,
            use_color=self.use_color,
            state="pending",
        )
        sys.stdout.write(line)
        sys.stdout.flush()
        self.active = True

    def succeed(self, label: str, *, detail_lines: list[str] | None = None) -> None:
        self.ensure_heading()
        self._finish(
            render_execution_status_line(
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
            render_execution_status_line(label, "failed", use_color=self.use_color, state="failure")
        )
        self._print_detail_lines(detail_lines or [])

    def close(self) -> None:
        if self.started:
            print()

    def _finish(self, line: str) -> None:
        if self.live and self.active:
            sys.stdout.write(self._erase_active_line())
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
            print(render_detail_line(f"  {line}" if line else "", use_color=self.use_color))

    def _success_word(self) -> str:
        return "loaded" if self.title == "Data" else "updated"

    def _erase_active_line(self) -> str:
        return "\r\033[2K"


def reject_build_flags_from_command(command: PublicationCommand, error: Callable[[str], None]) -> None:
    if command.clear_build:
        error(f"{command.command} does not accept --clear")


def render_section_heading(text: str, *, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{ANSI_BOLD}{ANSI_BLUE}{text}{ANSI_RESET}"


def render_detail_line(text: str, *, use_color: bool) -> str:
    if not use_color or not text:
        return text
    return f"{ANSI_WHITE}{text}{ANSI_RESET}"


def render_execution_status_line(label: str, status: str, *, use_color: bool, state: str) -> str:
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


def display_status(status: str) -> str:
    if status == "in-sync":
        return "unchanged"
    return status


def render_status_token(status: str, *, use_color: bool) -> str:
    return render_status_token_variant(status, use_color=use_color, padded=True)


def render_status_token_variant(status: str, *, use_color: bool, padded: bool) -> str:
    text = f"{status:<{STATUS_WIDTH}}" if padded else status
    if not use_color:
        return text
    color = STATUS_COLORS.get(status)
    if color is None:
        return text
    return f"{color}{text}{ANSI_RESET}"


def render_status_line(status: str, path: str, *, use_color: bool) -> str:
    shown = display_status(status)
    return f"{render_status_token(shown, use_color=use_color)} {path}"


def print_indented_lines(lines: Sequence[str], *, stream: object) -> None:
    use_color = stream is sys.stderr and sys.stderr.isatty()
    for line in lines:
        text = f"  {line}" if line else ""
        if use_color and text:
            text = f"{ANSI_WHITE}{text}{ANSI_RESET}"
        print(text, file=stream)
