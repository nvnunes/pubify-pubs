from __future__ import annotations

import pytest

from pubify_pubs.stats import (
    ComputedStat,
    ResolvedStat,
    compute_resolved_stat,
    ensure_unique_macro_names,
    macro_name_for_stat,
    normalize_stat_result,
    render_autostats_text,
)


def test_normalize_stat_result_accepts_single_string() -> None:
    result = normalize_stat_result("sample_count", "42")

    assert result == ((None, "42"),)


def test_normalize_stat_result_coerces_single_non_string_value() -> None:
    result = normalize_stat_result("sample_count", 42)

    assert result == ((None, "42"),)


def test_normalize_stat_result_accepts_named_dict() -> None:
    result = normalize_stat_result(
        "detection_summary",
        {"Count": 17, "Mean": 12},
    )

    assert result == (("Count", "17"), ("Mean", "12"))


def test_normalize_stat_result_rejects_empty_or_invalid_values() -> None:
    with pytest.raises(ValueError, match="must return a non-empty dict"):
        normalize_stat_result("sample_count", {})

    with pytest.raises(ValueError, match="dict keys must be non-empty strings"):
        normalize_stat_result("sample_count", {"": "42"})

    with pytest.raises(ValueError, match="non-empty after str\\(\\) coercion"):
        normalize_stat_result("sample_count", "")

    with pytest.raises(ValueError, match="non-empty after str\\(\\) coercion"):
        normalize_stat_result("sample_count", {"Count": ""})


def test_compute_resolved_stat_reuses_string_for_tex_and_display() -> None:
    computed = compute_resolved_stat("sample_count", "42")

    assert computed.values[0].macro_name == "StatSampleCount"
    assert computed.values[0].display == "42"
    assert computed.values[0].tex == "42"


def test_compute_resolved_stat_derives_display_from_texish_string() -> None:
    computed = compute_resolved_stat(
        "median_offset",
        {"Mean": r"1.37\,\mathrm{mas}"},
    )

    assert computed.values[0].macro_name == "StatMedianOffsetMean"
    assert computed.values[0].display == "1.37 mas"
    assert computed.values[0].tex == r"1.37\,\mathrm{mas}"


def test_compute_resolved_stat_builds_named_macro_names_from_dict_keys() -> None:
    computed = compute_resolved_stat(
        "detection_summary",
        {"Count": "17", "Mean": "0.708"},
    )

    assert [value.macro_name for value in computed.values] == [
        "StatDetectionSummaryCount",
        "StatDetectionSummaryMean",
    ]


def test_macro_name_for_stat_rejects_invalid_id() -> None:
    with pytest.raises(ValueError, match="Invalid stat id"):
        macro_name_for_stat("!!!")


def test_macro_name_for_stat_rejects_invalid_key() -> None:
    with pytest.raises(ValueError, match="Invalid stat key"):
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
                ResolvedStat("StatDetectionSummaryCount", "17", "17"),
                ResolvedStat("StatDetectionSummaryMean", "24", "24"),
            ),
        ),
    )

    assert render_autostats_text(stats) == "\n".join(
        [
            r"\newcommand{\StatDetectionSummaryCount}{17}",
            r"\newcommand{\StatDetectionSummaryMean}{24}",
            "",
        ]
    )
