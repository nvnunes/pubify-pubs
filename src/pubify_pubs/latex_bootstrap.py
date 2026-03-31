from __future__ import annotations

from dataclasses import dataclass
from string import ascii_lowercase

from pubify_pubs.export import FigureExport
from pubify_pubs.stats import ComputedStat
from pubify_pubs.tables import ComputedTable, macro_name_for_table


@dataclass(frozen=True)
class FigureLatexSpec:
    figure_id: str
    layout: str
    panel_count: int


def render_figure_latex(spec: FigureLatexSpec, *, subcaption: bool) -> str:
    if subcaption and spec.panel_count == 1:
        raise ValueError(
            f"Figure '{spec.figure_id}' has one panel; latex subcaption mode is only supported for multi-panel figures"
        )
    macro_name = _figure_layout_macro(spec.layout, spec.panel_count)
    panel_tokens = _figure_panel_tokens(spec, subcaption=subcaption)
    body_lines = [r"\figfloat", "    {", f"        \\{macro_name}"]
    body_lines.extend(f"        {{{token}}}" for token in panel_tokens)
    body_lines.extend(
        [
            "    }",
            "    [Example caption.]",
            f"    [fig:{spec.figure_id}]",
        ]
    )
    return "\n".join(body_lines)


def render_stat_latex(stat: ComputedStat) -> str:
    return "\n".join(rf"\{value.macro_name}{{}}" for value in stat.values)


def render_table_latex(table: ComputedTable) -> str:
    column_spec = "l" * table.width
    header = " & ".join(f"Column {index}" for index in range(1, table.width + 1)) + r" \\"
    macro_name = macro_name_for_table(table.table_id)
    body_lines: list[str] = []
    if len(table.body_texts) == 1:
        body_lines.append(rf"\{macro_name}")
    else:
        for index in range(1, len(table.body_texts) + 1):
            body_lines.append(rf"\multicolumn{{{table.width}}}{{l}}{{Body {index}}} \\")
            body_lines.append(rf"\{macro_name}{{{index}}}")
            if index != len(table.body_texts):
                body_lines.append(r"\hline")
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        rf"\begin{{tabular}}{{{column_spec}}}",
        r"\hline",
        header,
        r"\hline",
        *body_lines,
        r"\hline",
        r"\end{tabular}",
        r"\caption{Example caption.}",
        rf"\label{{tab:{table.table_id}}}",
        r"\end{table}",
    ]
    return "\n".join(lines)


def build_figure_latex_spec(figure_id: str, export: FigureExport) -> FigureLatexSpec:
    panel_count = len(export.panels)
    return FigureLatexSpec(
        figure_id=figure_id,
        layout=_effective_figure_latex_layout(export.layout, panel_count),
        panel_count=panel_count,
    )


def _figure_layout_macro(layout: str, panel_count: int) -> str:
    supported = {
        ("one", 1): "figone",
        ("onewide", 1): "figonewide",
        ("two", 2): "figtwo",
        ("twowide", 2): "figtwowide",
        ("three", 3): "figthree",
        ("threewide", 3): "figthreewide",
        ("four", 4): "figfour",
    }
    macro = supported.get((layout, panel_count))
    if macro is None:
        raise ValueError(
            f"Figure bootstrap does not support layout '{layout}' with {panel_count} panels"
        )
    return macro


def _effective_figure_latex_layout(layout: str | None, panel_count: int) -> str:
    if panel_count == 1:
        return "onewide" if layout == "onewide" else "one"
    if panel_count == 2:
        return "twowide" if layout == "twowide" else "two"
    if panel_count == 3:
        return "threewide" if layout == "threewide" else "three"
    if panel_count == 4:
        return "four"
    return layout or "one"


def _figure_panel_tokens(spec: FigureLatexSpec, *, subcaption: bool) -> tuple[str, ...]:
    paths = tuple(_figure_panel_path(spec.figure_id, spec.panel_count, index) for index in range(spec.panel_count))
    if not subcaption:
        return paths
    labels = _panel_labels(spec.panel_count)
    return tuple(
        rf"\fig{{{path}}}[Example subcaption][fig:{spec.figure_id}:{label}]"
        for path, label in zip(paths, labels, strict=True)
    )


def _figure_panel_path(figure_id: str, panel_count: int, index: int) -> str:
    if panel_count == 1:
        return f"autofigures/{figure_id}"
    return f"autofigures/{figure_id}_{index + 1}"


def _panel_labels(panel_count: int) -> tuple[str, ...]:
    if panel_count > len(ascii_lowercase):
        raise ValueError(f"Too many figure panels for subcaption labels: {panel_count}")
    return tuple(ascii_lowercase[index] for index in range(panel_count))
