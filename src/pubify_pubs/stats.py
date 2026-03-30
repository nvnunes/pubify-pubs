from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


AUTOSTATS_FILENAME = "autostats.tex"
_MACRO_NAME_PART = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class Stat:
    """One returned publication stat value for console display and TeX emission."""

    display: str
    tex: str | None = None
    suffix: str | None = None


@dataclass(frozen=True)
class ResolvedStat:
    """Normalized stat value paired with its final TeX macro name."""

    macro_name: str
    display: str
    tex: str


@dataclass(frozen=True)
class ComputedStat:
    """One computed stat id plus all emitted TeX-facing values."""

    stat_id: str
    values: tuple[ResolvedStat, ...]


def normalize_stat_result(stat_id: str, result: object) -> tuple[Stat, ...]:
    """Normalize one stat return value into a validated tuple of ``Stat`` objects."""

    if isinstance(result, Stat):
        values = (result,)
    elif isinstance(result, tuple):
        values = result
    elif isinstance(result, list):
        values = tuple(result)
    else:
        raise ValueError(
            f"Stat '{stat_id}' must return a Stat, tuple[Stat, ...], or list[Stat]"
        )

    if not values:
        raise ValueError(f"Stat '{stat_id}' must return at least one Stat")

    normalized: list[Stat] = []
    for index, item in enumerate(values):
        if not isinstance(item, Stat):
            raise ValueError(f"Stat '{stat_id}' returned a non-Stat value at position {index + 1}")
        if not isinstance(item.display, str) or not item.display:
            raise ValueError(f"Stat '{stat_id}' display must be a non-empty string")
        if item.tex is not None and (not isinstance(item.tex, str) or not item.tex):
            raise ValueError(f"Stat '{stat_id}' tex must be a non-empty string when set")
        if item.suffix is not None and (not isinstance(item.suffix, str) or not item.suffix):
            raise ValueError(f"Stat '{stat_id}' suffix must be a non-empty string when set")
        if index > 0 and item.suffix is None:
            raise ValueError(f"Stat '{stat_id}' additional values must set suffix")
        normalized.append(item)
    return tuple(normalized)


def compute_resolved_stat(stat_id: str, result: object) -> ComputedStat:
    """Resolve one stat return value into final macro names and TeX payloads."""

    values = normalize_stat_result(stat_id, result)
    resolved_values = tuple(
        ResolvedStat(
            macro_name=macro_name_for_stat(stat_id, item.suffix),
            display=item.display,
            tex=item.display if item.tex is None else item.tex,
        )
        for item in values
    )
    return ComputedStat(stat_id=stat_id, values=resolved_values)


def ensure_unique_macro_names(stats: tuple[ComputedStat, ...]) -> None:
    """Raise ``ValueError`` if multiple stats emit the same final macro name."""

    seen: dict[str, str] = {}
    for stat in stats:
        for value in stat.values:
            owner = seen.get(value.macro_name)
            if owner is not None:
                raise ValueError(
                    f"Stats '{owner}' and '{stat.stat_id}' both emit macro '\\{value.macro_name}'"
                )
            seen[value.macro_name] = stat.stat_id


def render_autostats_text(stats: tuple[ComputedStat, ...]) -> str:
    """Render one authoritative ``autostats.tex`` snapshot."""

    lines: list[str] = []
    for stat in stats:
        for value in stat.values:
            lines.append(rf"\newcommand{{\{value.macro_name}}}{{{value.tex}}}")
    return "\n".join(lines) + ("\n" if lines else "")


def autostats_path(tex_root: Path) -> Path:
    """Return the framework-owned generated stats file path under ``tex/``."""

    return tex_root / AUTOSTATS_FILENAME


def macro_name_for_stat(stat_id: str, suffix: str | None = None) -> str:
    """Return the final TeX macro name for one stat id and optional suffix."""

    base_name = "Stat" + _camel_case_token_string(stat_id, kind="stat id")
    if suffix is None:
        return base_name
    return base_name + _camel_case_token_string(suffix, kind="stat suffix")


def _camel_case_token_string(value: str, *, kind: str) -> str:
    tokens = _MACRO_NAME_PART.findall(value)
    if not tokens:
        raise ValueError(f"Invalid {kind} for TeX macro naming: {value!r}")
    return "".join(token[:1].upper() + token[1:] for token in tokens)
