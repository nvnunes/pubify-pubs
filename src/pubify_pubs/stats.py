from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import pubify_data


AUTOSTATS_FILENAME = "autostats.tex"
_MACRO_NAME_PART = re.compile(r"[A-Za-z0-9]+")
_DISPLAY_MATH_DELIMS = re.compile(r"^\$(.*)\$$")
_DISPLAY_COMMANDS = (
    (re.compile(r"\\,"), " "),
    (re.compile(r"\\mathrm\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\text\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\texttt\{([^}]*)\}"), r"\1"),
)


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


class StatResult(pubify_data.BaseStatResult):
    """Publication stat result with TeX-facing metadata reserved for pubify-pubs."""


def normalize_stat_result(stat_id: str, result: object) -> tuple[tuple[str | None, str], ...]:
    """Normalize one stat return value into a validated tuple of ``(key, value)`` pairs."""

    if not isinstance(result, dict):
        value = _coerce_stat_value(stat_id, result)
        return ((None, value),)
    if isinstance(result, dict):
        if not result:
            raise ValueError(f"Stat '{stat_id}' must return a non-empty dict when using named values")
        normalized: list[tuple[str | None, str]] = []
        for key, value in result.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"Stat '{stat_id}' dict keys must be non-empty strings")
            normalized.append((key, _coerce_stat_value(stat_id, value, key=key)))
        return tuple(normalized)


def _coerce_stat_value(stat_id: str, value: object, *, key: str | None = None) -> str:
    text = value if isinstance(value, str) else str(value)
    if not text:
        if key is None:
            raise ValueError(f"Stat '{stat_id}' value must be non-empty after str() coercion")
        raise ValueError(
            f"Stat '{stat_id}' dict value for key '{key}' must be non-empty after str() coercion"
        )
    return text


def compute_resolved_stat(stat_id: str, result: object) -> ComputedStat:
    """Resolve one stat return value into final macro names and TeX payloads."""

    values = _neutral_stat_values(result)
    if values is None:
        values = normalize_stat_result(stat_id, result)
    resolved_values = tuple(
        ResolvedStat(
            macro_name=macro_name_for_stat(stat_id, key),
            display=_display_from_tex(value),
            tex=value,
        )
        for key, value in values
    )
    return ComputedStat(stat_id=stat_id, values=resolved_values)


def _neutral_stat_values(result: object) -> tuple[tuple[str | None, str], ...] | None:
    values = getattr(result, "values", None)
    if values is None or callable(values):
        return None
    normalized: list[tuple[str | None, str]] = []
    for value in values:
        key = getattr(value, "key", None)
        text = getattr(value, "value", None)
        if text is None:
            return None
        normalized.append((key, str(text)))
    return tuple(normalized)


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


def macro_name_for_stat(stat_id: str, key: str | None = None) -> str:
    """Return the final TeX macro name for one stat id and optional keyed value name."""

    base_name = "Stat" + _camel_case_token_string(stat_id, kind="stat id")
    if key is None:
        return base_name
    return base_name + _camel_case_token_string(key, kind="stat key")


def _display_from_tex(value: str) -> str:
    stripped = value.strip()
    match = _DISPLAY_MATH_DELIMS.fullmatch(stripped)
    if match is not None:
        stripped = match.group(1)
    for pattern, replacement in _DISPLAY_COMMANDS:
        stripped = pattern.sub(replacement, stripped)
    stripped = stripped.replace("{", "").replace("}", "")
    stripped = stripped.replace("\\", "")
    return " ".join(stripped.split())


def _camel_case_token_string(value: str, *, kind: str) -> str:
    tokens = _MACRO_NAME_PART.findall(value)
    if not tokens:
        raise ValueError(f"Invalid {kind} for TeX macro naming: {value!r}")
    return "".join(token[:1].upper() + token[1:] for token in tokens)
