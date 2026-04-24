from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import math
from pathlib import Path
import re


AUTOTABLES_FILENAME = "autotables.tex"
_MACRO_NAME_PART = re.compile(r"[A-Za-z0-9]+")
_INPUT_RE = re.compile(r"\\(?:input|include)\{([^}]+)\}")
_TABLE_ENV_RE = re.compile(r"\\(begin|end)\{(tabular|tabularx|longtable)\}")


@dataclass(frozen=True, init=False)
class TableResult:
    """Normalized logical-table payload for the publication runtime."""

    bodies: tuple[tuple[tuple[object, ...], ...], ...]
    formats: tuple[str | None, ...] | None = None
    tex_wrappers: tuple[str | None, ...] | None = None
    multicolumns: tuple[tuple[object, ...], ...] = field(default_factory=tuple)
    metadata: dict[str, object] = field(default_factory=dict)

    def __init__(
        self,
        data: object,
        *,
        formats: Sequence[str | None] | None = None,
        tex_wrappers: Sequence[str | None] | None = None,
        multicolumns: Sequence[Sequence[object]] | None = None,
    ) -> None:
        bodies = _normalize_table_data(data)
        object.__setattr__(self, "bodies", bodies)
        object.__setattr__(self, "formats", tuple(formats) if formats is not None else None)
        object.__setattr__(
            self,
            "tex_wrappers",
            tuple(tex_wrappers) if tex_wrappers is not None else None,
        )
        object.__setattr__(
            self,
            "multicolumns",
            tuple(tuple(spec) for spec in (multicolumns or ())),
        )
        object.__setattr__(
            self,
            "metadata",
            {
                "formats": self.formats,
                "tex_wrappers": self.tex_wrappers,
                "multicolumns": self.multicolumns,
            },
        )
        self.__post_init__()

    def __post_init__(self) -> None:
        width = self.width
        if self.formats is not None and len(self.formats) != width:
            raise ValueError(
                f"TableResult formats length {len(self.formats)} does not match logical width {width}"
            )
        if self.tex_wrappers is not None and len(self.tex_wrappers) != width:
            raise ValueError(
                f"TableResult tex_wrappers length {len(self.tex_wrappers)} does not match logical width {width}"
            )
        if self.tex_wrappers is not None:
            for index, wrapper in enumerate(self.tex_wrappers):
                if wrapper in {None, ""}:
                    continue
                if wrapper.count("@") != 1:
                    raise ValueError(
                        f"TableResult tex_wrappers[{index}] must contain exactly one '@' placeholder"
                    )
        for spec in self.multicolumns:
            _validate_multicolumn_spec(spec, width)
            start, end = int(spec[0]), int(spec[1])
            expected_format = _effective_format(self.formats, start)
            expected_wrapper = _effective_wrapper(self.tex_wrappers, start)
            for column_index in range(start + 1, end + 1):
                if _effective_format(self.formats, column_index) != expected_format:
                    raise ValueError(
                        f"TableResult multicolumn span [{start}, {end}] requires identical column formats"
                    )
                if _effective_wrapper(self.tex_wrappers, column_index) != expected_wrapper:
                    raise ValueError(
                        f"TableResult multicolumn span [{start}, {end}] requires identical tex_wrappers"
                    )

    @property
    def width(self) -> int:
        return len(self.bodies[0][0])


@dataclass(frozen=True)
class ComputedTable:
    """One computed table id plus its rendered LaTeX body commands."""

    table_id: str
    width: int
    body_texts: tuple[str, ...]


def compute_table(table_id: str, result: object) -> ComputedTable:
    if hasattr(result, "bodies") and hasattr(result, "metadata") and not isinstance(result, TableResult):
        metadata = getattr(result, "metadata") or {}
        result = TableResult(
            getattr(result, "bodies"),
            formats=metadata.get("formats"),
            tex_wrappers=metadata.get("tex_wrappers"),
            multicolumns=metadata.get("multicolumns"),
        )
    if not isinstance(result, TableResult):
        raise ValueError(f"Table '{table_id}' must return TableResult(...)")
    body_texts = tuple(_render_body(result, body) for body in result.bodies)
    return ComputedTable(table_id=table_id, width=result.width, body_texts=body_texts)


def render_autotables_text(tables: tuple[ComputedTable, ...]) -> str:
    lines: list[str] = []
    for table in tables:
        macro_name = macro_name_for_table(table.table_id)
        if len(table.body_texts) == 1:
            lines.extend(
                [
                    rf"\newcommand{{\{macro_name}}}{{%",
                    table.body_texts[0],
                    "}",
                ]
            )
            continue
        lines.extend(
            [
                rf"\newcommand{{\{macro_name}}}[1]{{%",
                r"\ifcase#1%",
                rf"\PackageError{{pubify-pubs}}{{Table body index 0 is invalid for \string\{macro_name}}}{{}}%",
            ]
        )
        for body_text in table.body_texts:
            lines.extend([r"\or", body_text])
        lines.extend(
            [
                rf"\else\PackageError{{pubify-pubs}}{{Unknown body index for \string\{macro_name}: #1}}{{}}%",
                r"\fi",
                "}",
            ]
        )
    return "\n".join(lines) + ("\n" if lines else "")


def macro_name_for_table(table_id: str) -> str:
    return "Table" + _camel_case_token_string(table_id, kind="table id")


def autotables_path(tex_root: Path) -> Path:
    return tex_root / AUTOTABLES_FILENAME


def check_table_references(
    tex_root: Path,
    main_tex_path: Path,
    tables: tuple[ComputedTable, ...],
    *,
    table_id: str | None = None,
) -> None:
    selected = {table.table_id: table for table in tables if table_id is None or table.table_id == table_id}
    if not selected:
        return
    usages = _collect_table_usages(tex_root, main_tex_path, tuple(selected.values()))
    for current_id, table in selected.items():
        for usage in usages.get(current_id, ()):
            expected = table.width
            if usage.width != expected:
                raise ValueError(
                    f"Table '{current_id}' requires {expected} columns but enclosing "
                    f"{usage.environment} at {usage.file_path}:{usage.line_number} defines {usage.width}"
                )


@dataclass(frozen=True)
class _TableUsage:
    file_path: Path
    line_number: int
    environment: str
    width: int


def _render_body(result: TableResult, body: tuple[tuple[object, ...], ...]) -> str:
    rendered_rows = [_render_row(result, row) for row in body]
    return "\n".join(rendered_rows)


def _render_row(result: TableResult, row: tuple[object, ...]) -> str:
    cells: list[str] = []
    column_index = 0
    while column_index < len(row):
        spec = _multicolumn_spec_for_column(result.multicolumns, column_index)
        if spec is None:
            cells.append(
                _render_cell(
                    row[column_index],
                    _effective_format(result.formats, column_index),
                    _effective_wrapper(result.tex_wrappers, column_index),
                )
            )
            column_index += 1
            continue
        start, end = int(spec[0]), int(spec[1])
        span_values = row[start : end + 1]
        merged = _render_multicolumn(result, spec, span_values)
        if merged is None:
            cells.append(
                _render_cell(
                    row[column_index],
                    _effective_format(result.formats, column_index),
                    _effective_wrapper(result.tex_wrappers, column_index),
                )
            )
            column_index += 1
            continue
        cells.append(merged)
        column_index = end + 1
    return " & ".join(cells) + r" \\"


def _render_multicolumn(
    result: TableResult,
    spec: tuple[object, ...],
    values: tuple[object, ...],
) -> str | None:
    start, end = int(spec[0]), int(spec[1])
    width = end - start + 1
    if _all_missing(values):
        missing_display = "" if len(spec) < 3 else spec[2]
        missing_format = "{}" if len(spec) < 4 else spec[3]
        rendered = _render_formatted_value(missing_display, missing_format, None)
        return rf"\multicolumn{{{width}}}{{l}}{{{rendered}}}"
    if _all_equal_non_missing(values):
        rendered = _render_cell(
            values[0],
            _effective_format(result.formats, start),
            _effective_wrapper(result.tex_wrappers, start),
        )
        return rf"\multicolumn{{{width}}}{{l}}{{{rendered}}}"
    return None


def _render_cell(value: object, format_spec: str | None, wrapper: str | None) -> str:
    return _render_formatted_value(value, format_spec, wrapper)


def _render_formatted_value(value: object, format_spec: str | None, wrapper: str | None) -> str:
    raw_tex = format_spec == "tex"
    if raw_tex:
        rendered = str(value)
    elif format_spec in {None, "", "{}"}:
        rendered = str(value)
    else:
        rendered = format_spec.format(value)
    insertion = rendered if raw_tex else _escape_latex(rendered)
    if wrapper in {None, ""}:
        return insertion
    if wrapper.count("@") != 1:
        raise ValueError("tex wrapper must contain exactly one '@' placeholder")
    return wrapper.replace("@", insertion)


def _validate_multicolumn_spec(spec: tuple[object, ...], width: int) -> None:
    if len(spec) < 2 or len(spec) > 4:
        raise ValueError("Each multicolumn spec must be [start, end], [start, end, missing_display], or [start, end, missing_display, missing_format]")
    if not isinstance(spec[0], int) or not isinstance(spec[1], int):
        raise ValueError("multicolumn start and end indices must be integers")
    start, end = spec[0], spec[1]
    if start < 0 or end < start or end >= width:
        raise ValueError(f"Invalid multicolumn span [{start}, {end}] for logical width {width}")
    if len(spec) >= 4 and not isinstance(spec[3], str):
        raise ValueError("multicolumn missing_format must be a string when set")


def _effective_format(formats: tuple[str | None, ...] | None, index: int) -> str | None:
    if formats is None:
        return "{}"
    return formats[index]


def _effective_wrapper(tex_wrappers: tuple[str | None, ...] | None, index: int) -> str | None:
    if tex_wrappers is None:
        return None
    return tex_wrappers[index]


def _multicolumn_spec_for_column(
    multicolumns: tuple[tuple[object, ...], ...],
    column_index: int,
) -> tuple[object, ...] | None:
    for spec in multicolumns:
        start, end = int(spec[0]), int(spec[1])
        if start == column_index:
            return spec
        if start < column_index <= end:
            return spec
    return None


def _normalize_table_data(data: object) -> tuple[tuple[tuple[object, ...], ...], ...]:
    if hasattr(data, "ndim") and hasattr(data, "tolist"):
        ndim = getattr(data, "ndim")
        if ndim == 2:
            return (_normalize_body(data.tolist()),)
        if ndim == 3:
            return tuple(_normalize_body(body) for body in data.tolist())
        raise ValueError("TableResult data must be a 2D or 3D array-like value")
    if not _is_nested_sequence(data):
        raise ValueError("TableResult data must be a 2D or 3D array-like value")
    top = tuple(data)
    if not top:
        raise ValueError("TableResult data must contain at least one body or row")
    if all(_is_row_sequence(item) for item in top):
        return (_normalize_body(top),)
    if all(_is_nested_sequence(item) for item in top):
        return tuple(_normalize_body(item) for item in top)
    raise ValueError("TableResult data must be a 2D or 3D array-like value")


def _normalize_body(body: object) -> tuple[tuple[object, ...], ...]:
    if not _is_nested_sequence(body):
        raise ValueError("Each table body must be a 2D array-like value")
    rows = tuple(body)
    if not rows:
        raise ValueError("Each table body must contain at least one row")
    normalized_rows: list[tuple[object, ...]] = []
    expected_width: int | None = None
    for row in rows:
        if not _is_row_sequence(row):
            raise ValueError("Table body rows must be one-dimensional sequences of cell values")
        normalized_row = tuple(row)
        if not normalized_row:
            raise ValueError("Table body rows must contain at least one column")
        if expected_width is None:
            expected_width = len(normalized_row)
        elif len(normalized_row) != expected_width:
            raise ValueError("All rows in a table body must have the same logical width")
        normalized_rows.append(normalized_row)
    return tuple(normalized_rows)


def _is_nested_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _is_row_sequence(value: object) -> bool:
    if not _is_nested_sequence(value):
        return False
    return all(not _is_nested_sequence(cell) for cell in value)


def _all_missing(values: tuple[object, ...]) -> bool:
    return all(_is_missing(value) for value in values)


def _all_equal_non_missing(values: tuple[object, ...]) -> bool:
    if not values or any(_is_missing(value) for value in values):
        return False
    first = values[0]
    return all(value == first for value in values[1:])


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return math.isnan(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _escape_latex(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in text)


def _camel_case_token_string(value: str, *, kind: str) -> str:
    tokens = _MACRO_NAME_PART.findall(value)
    if not tokens:
        raise ValueError(f"Invalid {kind} for TeX macro naming: {value!r}")
    return "".join(token[:1].upper() + token[1:] for token in tokens)


def _collect_table_usages(
    tex_root: Path,
    main_tex_path: Path,
    tables: tuple[ComputedTable, ...],
) -> dict[str, tuple[_TableUsage, ...]]:
    files = _collect_manuscript_files(tex_root / main_tex_path)
    macro_names = {table.table_id: macro_name_for_table(table.table_id) for table in tables}
    usages: dict[str, list[_TableUsage]] = {table.table_id: [] for table in tables}
    for file_path in files:
        text = file_path.read_text(encoding="utf-8")
        environments = _find_table_environments(text, file_path)
        for table_id, macro_name in macro_names.items():
            for match in re.finditer(rf"\\{macro_name}(?:\{{\d+\}})?", text):
                usage = _enclosing_environment(environments, match.start())
                if usage is None:
                    raise ValueError(
                        f"Table '{table_id}' must be used directly inside a supported table environment: "
                        f"{file_path}:{_line_number(text, match.start())}"
                    )
                usages[table_id].append(usage)
    return {table_id: tuple(entries) for table_id, entries in usages.items()}


def _collect_manuscript_files(main_tex_file: Path) -> tuple[Path, ...]:
    seen: set[Path] = set()
    collected: list[Path] = []
    generated_autotables = autotables_path(main_tex_file.parent).resolve()

    def visit(path: Path) -> None:
        resolved = path.resolve()
        if resolved == generated_autotables:
            return
        if resolved in seen or not resolved.exists():
            return
        seen.add(resolved)
        collected.append(resolved)
        text = resolved.read_text(encoding="utf-8")
        for match in _INPUT_RE.finditer(text):
            child = Path(match.group(1))
            if child.suffix != ".tex":
                child = child.with_suffix(".tex")
            visit((resolved.parent / child).resolve())

    visit(main_tex_file)
    return tuple(collected)


def _find_table_environments(text: str, file_path: Path) -> tuple[tuple[int, int, _TableUsage], ...]:
    stack: list[tuple[str, int, int]] = []
    environments: list[tuple[int, int, _TableUsage]] = []
    for match in _TABLE_ENV_RE.finditer(text):
        kind, env_name = match.group(1), match.group(2)
        if kind == "begin":
            width = _parse_environment_width(text, match.end(), env_name)
            stack.append((env_name, match.start(), width))
            continue
        for index in range(len(stack) - 1, -1, -1):
            current_env, start_pos, width = stack[index]
            if current_env != env_name:
                continue
            usage = _TableUsage(
                file_path=file_path,
                line_number=_line_number(text, start_pos),
                environment=env_name,
                width=width,
            )
            environments.append((start_pos, match.end(), usage))
            del stack[index]
            break
    return tuple(environments)


def _enclosing_environment(
    environments: tuple[tuple[int, int, _TableUsage], ...],
    position: int,
) -> _TableUsage | None:
    enclosing: _TableUsage | None = None
    enclosing_span: tuple[int, int] | None = None
    for start, end, usage in environments:
        if start < position < end:
            if enclosing_span is None or (start >= enclosing_span[0] and end <= enclosing_span[1]):
                enclosing = usage
                enclosing_span = (start, end)
    return enclosing


def _parse_environment_width(text: str, position: int, environment: str) -> int:
    index = _skip_whitespace(text, position)
    if index < len(text) and text[index] == "[":
        _, index = _read_bracketed_group(text, index, "[", "]")
        index = _skip_whitespace(text, index)
    if environment == "tabularx":
        _, index = _read_bracketed_group(text, index, "{", "}")
        index = _skip_whitespace(text, index)
    colspec, _ = _read_bracketed_group(text, index, "{", "}")
    return _count_columns_in_spec(colspec)


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _read_bracketed_group(text: str, index: int, opener: str, closer: str) -> tuple[str, int]:
    if index >= len(text) or text[index] != opener:
        raise ValueError(f"Unsupported table syntax near: expected '{opener}'")
    depth = 0
    start = index + 1
    while index < len(text):
        char = text[index]
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start:index], index + 1
        index += 1
    raise ValueError(f"Unsupported table syntax near: unterminated '{opener}{closer}' group")


def _count_columns_in_spec(spec: str) -> int:
    index = 0
    count = 0
    while index < len(spec):
        char = spec[index]
        if char.isspace() or char == "|":
            index += 1
            continue
        if char in "lcrX":
            count += 1
            index += 1
            continue
        if char in "pmb":
            _, index = _read_bracketed_group(spec, index + 1, "{", "}")
            count += 1
            continue
        if char in "@!<>":
            _, index = _read_bracketed_group(spec, index + 1, "{", "}")
            continue
        if char == "*":
            repeat_text, next_index = _read_bracketed_group(spec, index + 1, "{", "}")
            inner_spec, index = _read_bracketed_group(spec, next_index, "{", "}")
            try:
                repeat_count = int(repeat_text.strip())
            except ValueError as exc:
                raise ValueError(f"Unsupported table column repeat count: {repeat_text!r}") from exc
            count += repeat_count * _count_columns_in_spec(inner_spec)
            continue
        raise ValueError(f"Unsupported table column specification syntax: {spec!r}")
    return count


def _line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1
