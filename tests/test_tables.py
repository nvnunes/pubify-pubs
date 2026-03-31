from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pubify_pubs.tables import (
    TableResult,
    check_table_references,
    compute_table,
    macro_name_for_table,
    render_autotables_text,
)


def test_table_result_accepts_2d_and_3d_array_like_data() -> None:
    single = TableResult([["A", 1], ["B", 2]])
    multi = TableResult(np.array([[["A", 1]], [["B", 2]]], dtype=object))

    assert single.width == 2
    assert len(single.bodies) == 1
    assert multi.width == 2
    assert len(multi.bodies) == 2


def test_table_result_rejects_non_rectangular_data() -> None:
    with pytest.raises(ValueError, match="same logical width"):
        TableResult([["A", 1], ["B"]])


def test_table_result_rejects_invalid_tex_wrapper() -> None:
    with pytest.raises(ValueError, match="exactly one '@'"):
        TableResult([["A"]], tex_wrappers=[r"\mathrm{value}"])


def test_compute_table_renders_formats_tex_wrappers_and_multicolumns() -> None:
    computed = compute_table(
        "summary",
        TableResult(
            [
                ["Primary", "Primary"],
                ["Offset", "1.37"],
                [None, None],
            ],
            formats=["{}", "{}"],
            multicolumns=[
                [0, 1, r"\cdots", "tex"],
            ],
        ),
    )

    assert computed.width == 2
    assert r"\multicolumn{2}{l}{Primary} \\" in computed.body_texts[0]
    assert r"Offset & 1.37 \\" in computed.body_texts[0]
    assert r"\multicolumn{2}{l}{\cdots} \\" in computed.body_texts[0]


def test_compute_table_renders_tex_wrappers_for_non_merged_cells() -> None:
    computed = compute_table(
        "summary",
        TableResult(
            [["Offset", 1.372]],
            formats=["{}", "{:.2f}"],
            tex_wrappers=[None, r"@\,\mathrm{mas}"],
        ),
    )

    assert r"Offset & 1.37\,\mathrm{mas} \\" in computed.body_texts[0]


def test_table_result_rejects_multicolumn_format_mismatch() -> None:
    with pytest.raises(ValueError, match="identical column formats"):
        TableResult(
            [[1, 1]],
            formats=["{}", "{:.2f}"],
            multicolumns=[[0, 1]],
        )


def test_render_autotables_text_supports_single_and_multi_body_macros() -> None:
    single = compute_table("summary", TableResult([["A", "B"]]))
    multi = compute_table("split", TableResult([[["A", "B"]], [["C", "D"]]]))

    text = render_autotables_text((single, multi))

    assert r"\newcommand{\TableSummary}{" in text
    assert r"\newcommand{\TableSplit}[1]{" in text
    assert r"\ifcase#1%" in text


def test_check_table_references_validates_width(tmp_path: Path) -> None:
    tex_root = tmp_path / "tex"
    tex_root.mkdir(parents=True)
    main_tex = tex_root / "main.tex"
    main_tex.write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                r"\begin{document}",
                r"\input{autotables.tex}",
                r"\begin{tabular}{ll}",
                r"\TableSummary",
                r"\end{tabular}",
                r"\end{document}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    table = compute_table("summary", TableResult([["A", "B"]]))

    check_table_references(tex_root, Path("main.tex"), (table,))

    bad_table = compute_table("summary", TableResult([["A", "B", "C"]]))
    with pytest.raises(ValueError, match="requires 3 columns"):
        check_table_references(tex_root, Path("main.tex"), (bad_table,))


def test_check_table_references_rejects_unsupported_wrapper_usage(tmp_path: Path) -> None:
    tex_root = tmp_path / "tex"
    tex_root.mkdir(parents=True)
    main_tex = tex_root / "main.tex"
    main_tex.write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                r"\begin{document}",
                r"\newcommand{\Wrapper}{\TableSummary}",
                r"\begin{tabular}{ll}",
                r"\Wrapper",
                r"\end{tabular}",
                r"\end{document}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    table = compute_table("summary", TableResult([["A", "B"]]))

    with pytest.raises(ValueError, match="must be used directly inside a supported table environment"):
        check_table_references(tex_root, Path("main.tex"), (table,))


def test_check_table_references_ignores_generated_autotables_file(tmp_path: Path) -> None:
    tex_root = tmp_path / "tex"
    tex_root.mkdir(parents=True)
    (tex_root / "autotables.tex").write_text(
        r"\newcommand{\TableSummary}{A & B \\}" + "\n",
        encoding="utf-8",
    )
    main_tex = tex_root / "main.tex"
    main_tex.write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                r"\begin{document}",
                r"\input{autotables.tex}",
                r"\begin{tabular}{ll}",
                r"\TableSummary",
                r"\end{tabular}",
                r"\end{document}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    table = compute_table("summary", TableResult([["A", "B"]]))

    check_table_references(tex_root, Path("main.tex"), (table,))


def test_macro_name_for_table_uses_camel_case() -> None:
    assert macro_name_for_table("training_summary") == "TableTrainingSummary"
