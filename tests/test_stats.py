from __future__ import annotations

import pytest

from pubify_pubs.stats import (
    ComputedStat,
    ResolvedStat,
    Stat,
    compute_resolved_stat,
    ensure_unique_macro_names,
    macro_name_for_stat,
    normalize_stat_result,
    render_autostats_text,
)


def test_normalize_stat_result_accepts_single_stat() -> None:
    result = normalize_stat_result("sample_count", Stat(display="42"))

    assert result == (Stat(display="42"),)


def test_normalize_stat_result_accepts_tuple_and_list() -> None:
    tuple_result = normalize_stat_result(
        "detection_summary",
        (Stat(display="17"), Stat(display="24", suffix="Total")),
    )
    list_result = normalize_stat_result(
        "detection_summary",
        [Stat(display="17"), Stat(display="24", suffix="Total")],
    )

    assert len(tuple_result) == 2
    assert len(list_result) == 2


def test_normalize_stat_result_allows_suffix_on_primary_value() -> None:
    result = normalize_stat_result("sample_count", Stat(display="42", suffix="Value"))

    assert result == (Stat(display="42", suffix="Value"),)


def test_normalize_stat_result_requires_suffix_on_additional_values() -> None:
    with pytest.raises(ValueError, match="additional values must set suffix"):
        normalize_stat_result(
            "sample_count",
            (Stat(display="42"), Stat(display="24")),
        )


def test_compute_resolved_stat_reuses_display_for_tex_when_tex_missing() -> None:
    computed = compute_resolved_stat("sample_count", Stat(display="42"))

    assert computed.values[0].macro_name == "StatSampleCount"
    assert computed.values[0].display == "42"
    assert computed.values[0].tex == "42"


def test_compute_resolved_stat_preserves_distinct_tex_value() -> None:
    computed = compute_resolved_stat(
        "median_offset",
        Stat(display="1.37 mas", tex=r"1.37\,\mathrm{mas}"),
    )

    assert computed.values[0].macro_name == "StatMedianOffset"
    assert computed.values[0].display == "1.37 mas"
    assert computed.values[0].tex == r"1.37\,\mathrm{mas}"


def test_compute_resolved_stat_builds_primary_and_suffixed_macro_names() -> None:
    computed = compute_resolved_stat(
        "detection_summary",
        (
            Stat(display="17"),
            Stat(display="24", suffix="Total"),
            Stat(display="70.8%", tex="0.708", suffix="Fraction"),
        ),
    )

    assert [value.macro_name for value in computed.values] == [
        "StatDetectionSummary",
        "StatDetectionSummaryTotal",
        "StatDetectionSummaryFraction",
    ]


def test_compute_resolved_stat_uses_suffix_on_primary_macro_when_provided() -> None:
    computed = compute_resolved_stat(
        "sample_count",
        Stat(display="42", suffix="Value"),
    )

    assert [value.macro_name for value in computed.values] == ["StatSampleCountValue"]


def test_macro_name_for_stat_rejects_invalid_id() -> None:
    with pytest.raises(ValueError, match="Invalid stat id"):
        macro_name_for_stat("!!!")


def test_macro_name_for_stat_rejects_invalid_suffix() -> None:
    with pytest.raises(ValueError, match="Invalid stat suffix"):
        macro_name_for_stat("sample_count", "!!!")


def test_ensure_unique_macro_names_rejects_duplicates() -> None:
    stats = (
        ComputedStat(
            stat_id="sample_count",
            values=(ResolvedStat(macro_name="StatSampleCount", display="42", tex="42"),),
        ),
        ComputedStat(
            stat_id="other",
            values=(ResolvedStat(macro_name="StatSampleCount", display="43", tex="43"),),
        ),
    )

    with pytest.raises(ValueError, match=r"both emit macro '\\StatSampleCount'"):
        ensure_unique_macro_names(stats)


def test_render_autostats_text_is_deterministic() -> None:
    stats = (
        ComputedStat(
            stat_id="detection_summary",
            values=(
                ResolvedStat("StatDetectionSummary", "17", "17"),
                ResolvedStat("StatDetectionSummaryTotal", "24", "24"),
            ),
        ),
    )

    assert render_autostats_text(stats) == "\n".join(
        [
            r"\newcommand{\StatDetectionSummary}{17}",
            r"\newcommand{\StatDetectionSummaryTotal}{24}",
            "",
        ]
    )
