from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class LatexDiagnostic:
    summary: str
    source: str | None = None
    context: str | None = None


PRIMARY_SIGNAL_PATTERNS = (
    "LaTeX Error:",
    "I can't find file",
    "Undefined control sequence",
    "Emergency stop",
    "Runaway argument",
    "Missing ",
    "Fatal error occurred",
)
SOURCE_PATTERN = re.compile(r"^[^:\n]+:\d+:")
CONTEXT_PATTERN = re.compile(r"^l\.\d+")


def build_log_path(build_root: Path, main_tex_path: Path) -> Path:
    return build_root / main_tex_path.with_suffix(".log").name


def extract_latex_diagnostic(log_path: Path) -> LatexDiagnostic | None:
    if not log_path.exists():
        return None

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    candidates = [
        (index, lines[index].strip())
        for index in range(len(lines))
        if _is_primary_signal(lines[index].strip())
    ]
    if not candidates:
        return None

    index, stripped = max(
        candidates,
        key=lambda item: (_signal_priority(item[1]), item[0]),
    )
    summary = _normalize_summary(stripped)
    source = _find_adjacent(lines, index, SOURCE_PATTERN)
    context = _find_adjacent(lines, index, CONTEXT_PATTERN)
    return LatexDiagnostic(summary=summary, source=source, context=context)


def _is_primary_signal(line: str) -> bool:
    if line.startswith("! "):
        return True
    if line.startswith("Package ") and " Error:" in line:
        return True
    return any(pattern in line for pattern in PRIMARY_SIGNAL_PATTERNS)


def _normalize_summary(line: str) -> str:
    if line.startswith("! "):
        return line[2:].strip()
    return line.strip()


def _signal_priority(line: str) -> int:
    if "LaTeX Error:" in line or ("Package " in line and " Error:" in line):
        return 3
    if "I can't find file" in line:
        return 3
    if "Undefined control sequence" in line:
        return 2
    if "Runaway argument" in line or "Missing " in line or "Fatal error occurred" in line:
        return 1
    if "Emergency stop" in line:
        return 0
    if line.startswith("! "):
        return 1
    return 0


def _find_adjacent(lines: list[str], index: int, pattern: re.Pattern[str]) -> str | None:
    for offset in (-2, -1, 1, 2, 3):
        neighbor = index + offset
        if neighbor < 0 or neighbor >= len(lines):
            continue
        candidate = lines[neighbor].strip()
        if pattern.search(candidate):
            return candidate
    return None
