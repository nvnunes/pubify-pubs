from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from pubify_pubs.config import PublicationConfig, PubifyMplConfig, load_publication_config
from pubify_pubs.export import FigureExport, export_figure, normalize_figure_result, panel


class UnsupportedObject:
    pass


class FakePubifyBackend:
    def __init__(self) -> None:
        self.prepare_calls: list[tuple[Path, dict[str, object]]] = []
        self.save_calls: list[tuple[object, str, Path, dict[str, object], dict[str, object]]] = []

    def prepare(self, destination: Path, template: dict[str, object]) -> tuple[Path, Path]:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        style_path = destination / "pubify.sty"
        template_path = destination / "pubify-template.tex"
        style_path.write_text("% pubify\n", encoding="utf-8")
        template_path.write_text(str(dict(template)), encoding="utf-8")
        self.prepare_calls.append((destination, dict(template)))
        return style_path, template_path

    def save_fig(
        self,
        fig_or_ax: object,
        layout: str,
        filename: Path,
        *,
        template: dict[str, object] | None = None,
        **kwargs: object,
    ) -> None:
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        label = getattr(fig_or_ax, "_pubs_name", None)
        if label is None and hasattr(fig_or_ax, "figure"):
            label = getattr(fig_or_ax.figure, "_pubs_name", None)
        path.write_text(label or "panel", encoding="utf-8")
        self.save_calls.append((fig_or_ax, layout, path, dict(template or {}), dict(kwargs)))


@pytest.fixture()
def paper_config() -> PublicationConfig:
    return PublicationConfig(
        publication_id="demo",
        main_tex="main.tex",
        pubify_mpl=PubifyMplConfig(
            template={
                "textwidth_in": 6.75,
                "textheight_in": 9.7,
                "base_fontsize_pt": 10,
            },
            defaults={
                "layout": "onewide",
                "dpi": 144,
                "hide_labels": True,
            },
        ),
    )


def test_load_publication_config_parses_pubify_mpl_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "pub.yaml"
    config_path.write_text(
        "\n".join(
            [
                "publication_id: demo",
                "main_tex: main.tex",
                "pubify-mpl-template:",
                "  textwidth_in: 6.75",
                "  textheight_in: 9.7",
                "  base_fontsize_pt: 10",
                "pubify-mpl-defaults:",
                "  layout: twowide",
                "  dpi: 300",
                "  hide_labels: true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = load_publication_config(config_path, "demo")
    assert config.pubify_mpl.template["textwidth_in"] == 6.75
    assert config.pubify_mpl.template["base_fontsize_pt"] == 10
    assert config.pubify_mpl.default_layout == "twowide"
    assert config.pubify_mpl.defaults["hide_labels"] is True


def test_normalize_simple_returns_uses_publication_default_layout(paper_config: PublicationConfig) -> None:
    fig = plt.figure()
    ax1 = plt.figure().subplots()
    ax2 = plt.figure().subplots()
    single = normalize_figure_result(fig, paper_config)
    multi = normalize_figure_result([ax1, ax2], paper_config)

    assert single.layout == "onewide"
    assert len(single.panels) == 1
    assert single.panels[0].figure is fig
    assert multi.layout == "onewide"
    assert len(multi.panels) == 2
    assert multi.panels[0].figure is ax1
    assert multi.panels[1].figure is ax2
    plt.close(fig)
    plt.close(ax1.figure)
    plt.close(ax2.figure)


def test_normalize_simple_return_accepts_one_axes(paper_config: PublicationConfig) -> None:
    fig, ax = plt.subplots()

    result = normalize_figure_result(ax, paper_config)

    assert result.layout == "onewide"
    assert len(result.panels) == 1
    assert result.panels[0].figure is ax
    plt.close(fig)


def test_normalize_rejects_unsupported_object_type(paper_config: PublicationConfig) -> None:
    with pytest.raises(ValueError, match="Matplotlib Figure or Axes"):
        normalize_figure_result(UnsupportedObject(), paper_config)


def test_export_figure_single_panel_uses_shared_layout_and_kwargs(
    tmp_path: Path,
    paper_config: PublicationConfig,
) -> None:
    backend = FakePubifyBackend()
    tex_root = tmp_path / "tex"
    output_dir = tmp_path / "png-output"
    fig = plt.figure()
    fig._pubs_name = "single"
    result = FigureExport(
        panels=(panel(fig),),
        layout="twowide",
        kwargs={"caption_lines": 2},
    )

    paths = export_figure(
        paper_config,
        tex_root,
        output_dir,
        "compare",
        result,
        ".png",
        backend=backend,
    )

    assert [path.name for path in paths] == ["compare.png"]
    assert backend.prepare_calls == []
    _, layout, filename, template, kwargs = backend.save_calls[0]
    assert layout == "twowide"
    assert filename == output_dir / "compare.png"
    assert template == paper_config.pubify_mpl.template
    assert kwargs == {"dpi": 144, "hide_labels": True, "caption_lines": 2}


def test_export_figure_multi_panel_shared_metadata_only(
    tmp_path: Path,
    paper_config: PublicationConfig,
) -> None:
    backend = FakePubifyBackend()
    fig1 = plt.figure()
    fig1._pubs_name = "left"
    fig2 = plt.figure()
    fig2._pubs_name = "right"
    result = FigureExport(
        panels=(panel(fig1), panel(fig2)),
        layout="twowide",
        kwargs={"hide_annotations": True},
    )

    paths = export_figure(
        paper_config,
        tmp_path / "tex",
        tmp_path / "figures",
        "summary",
        result,
        ".pdf",
        backend=backend,
    )

    assert [path.name for path in paths] == ["summary_1.pdf", "summary_2.pdf"]
    assert backend.save_calls[0][4]["hide_annotations"] is True
    assert backend.save_calls[1][4]["hide_annotations"] is True


def test_export_figure_multi_panel_supports_per_panel_overrides(
    tmp_path: Path,
    paper_config: PublicationConfig,
) -> None:
    backend = FakePubifyBackend()
    fig1 = plt.figure()
    fig1._pubs_name = "left"
    fig2 = plt.figure()
    fig2._pubs_name = "right"
    result = FigureExport(
        panels=(
            panel(fig1, hide_labels=False),
            panel(fig2, hide_cbar=True),
        ),
        layout="twowide",
        kwargs={"caption_lines": 1},
    )

    export_figure(
        paper_config,
        tmp_path / "tex",
        tmp_path / "png-output",
        "compare",
        result,
        ".png",
        backend=backend,
    )

    assert backend.save_calls[0][4] == {
        "dpi": 144,
        "hide_labels": False,
        "caption_lines": 1,
    }
    assert backend.save_calls[1][4] == {
        "dpi": 144,
        "hide_labels": True,
        "caption_lines": 1,
        "hide_cbar": True,
    }


def test_typed_export_accepts_axes_panel(
    tmp_path: Path,
    paper_config: PublicationConfig,
) -> None:
    backend = FakePubifyBackend()
    fig, ax = plt.subplots()
    result = FigureExport(
        panels=(panel(ax),),
        layout="twowide",
    )

    paths = export_figure(
        paper_config,
        tmp_path / "tex",
        tmp_path / "png-output",
        "axes",
        result,
        ".png",
        backend=backend,
    )

    assert [path.name for path in paths] == ["axes.png"]
    assert backend.save_calls[0][0] is ax
    assert fig.number not in plt.get_fignums()


def test_png_and_pdf_exports_share_pubify_inputs_except_destination_and_extension(
    tmp_path: Path,
    paper_config: PublicationConfig,
) -> None:
    png_backend = FakePubifyBackend()
    export_backend = FakePubifyBackend()
    png_fig = plt.figure()
    png_fig._pubs_name = "single"
    export_fig = plt.figure()
    export_fig._pubs_name = "single"
    png_result = FigureExport(
        panels=(panel(png_fig),),
        layout="twowide",
        kwargs={"caption_lines": 2},
    )
    export_result = FigureExport(
        panels=(panel(export_fig),),
        layout="twowide",
        kwargs={"caption_lines": 2},
    )

    png_paths = export_figure(
        paper_config,
        tmp_path / "tex",
        tmp_path / "png-output",
        "figure",
        png_result,
        ".png",
        backend=png_backend,
    )
    export_paths = export_figure(
        paper_config,
        tmp_path / "tex",
        tmp_path / "pdf-output",
        "figure",
        export_result,
        ".pdf",
        backend=export_backend,
    )

    assert png_paths[0].name == "figure.png"
    assert export_paths[0].name == "figure.pdf"
    assert png_backend.prepare_calls == []
    assert export_backend.prepare_calls == []
    assert png_backend.save_calls[0][1] == export_backend.save_calls[0][1]
    assert png_backend.save_calls[0][3] == export_backend.save_calls[0][3]
    assert png_backend.save_calls[0][4] == export_backend.save_calls[0][4]
    assert png_backend.save_calls[0][2].suffix == ".png"
    assert export_backend.save_calls[0][2].suffix == ".pdf"


def test_export_figure_closes_real_matplotlib_figures(
    tmp_path: Path,
    paper_config: PublicationConfig,
) -> None:
    backend = FakePubifyBackend()
    fig1 = plt.figure()
    fig2 = plt.figure()
    initial_numbers = {fig1.number, fig2.number}
    assert initial_numbers.issubset(set(plt.get_fignums()))

    result = FigureExport(
        panels=(panel(fig1), panel(fig2)),
        layout="twowide",
    )

    export_figure(
        paper_config,
        tmp_path / "tex",
        tmp_path / "png-output",
        "batch",
        result,
        ".png",
        backend=backend,
    )

    remaining_numbers = set(plt.get_fignums())
    assert not (initial_numbers & remaining_numbers)
