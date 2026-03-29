from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from matplotlib.axes import Axes
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import pubify_mpl

from pubify_pubs.config import PublicationConfig, PubifyMplConfig


@dataclass(frozen=True)
class FigurePanel:
    """One exported panel plus optional per-panel export metadata and overrides."""

    figure: object
    subcaption_lines: int | None = None
    overrides: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class FigureExport:
    """Normalized logical-figure export payload for the publication runtime."""

    panels: tuple[FigurePanel, ...]
    layout: str | None = None
    caption_lines: int | None = None
    subcaption_lines: int | None = None
    kwargs: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.panels:
            raise ValueError("FigureExport requires at least one panel")
        if self.layout is not None and not self.layout:
            raise ValueError("FigureExport requires a non-empty layout")
        if not all(_is_pubify_export_target(panel.figure) for panel in self.panels):
            raise ValueError("Each FigureExport panel must contain a Matplotlib Figure or Axes")
        if self.caption_lines is not None and self.caption_lines < 1:
            raise ValueError("FigureExport caption_lines must be >= 1 when set")
        if self.subcaption_lines is not None and self.subcaption_lines < 1:
            raise ValueError("FigureExport subcaption_lines must be >= 1 when set")
        for panel_item in self.panels:
            if panel_item.subcaption_lines is not None and panel_item.subcaption_lines < 1:
                raise ValueError("FigurePanel subcaption_lines must be >= 1 when set")


def panel(
    figure: object,
    *,
    subcaption_lines: int | None = None,
    **overrides: object,
) -> FigurePanel:
    """Wrap one panel with optional per-panel subcaption sizing and overrides."""

    return FigurePanel(
        figure=figure,
        subcaption_lines=subcaption_lines,
        overrides=dict(overrides),
    )


def normalize_figure_result(result: object, config: PublicationConfig) -> FigureExport:
    """Normalize supported figure return values into a ``FigureExport``."""

    if result is None:
        raise ValueError("Figure returned None")
    if isinstance(result, FigureExport):
        return _with_default_layout(result, config)
    if _is_pubify_export_target(result):
        return FigureExport(
            panels=(FigurePanel(result),),
            layout=config.pubify_mpl.default_layout,
        )
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
        panels = tuple(result)
        if not panels:
            raise ValueError("Figure returned an empty sequence")
        if all(isinstance(item, FigurePanel) for item in panels):
            return FigureExport(panels=panels, layout=config.pubify_mpl.default_layout)
        if all(_is_pubify_export_target(item) for item in panels):
            return FigureExport(
                panels=tuple(FigurePanel(item) for item in panels),
                layout=config.pubify_mpl.default_layout,
            )
    raise ValueError(
        "Figure must return a Matplotlib Figure or Axes, a sequence of those objects, "
        "or FigureExport"
    )


def export_figure(
    config: PublicationConfig,
    tex_root: Path,
    output_dir: Path,
    figure_id: str,
    result: FigureExport,
    mode_extension: str,
    subfigure_index: int | None = None,
    backend: object | None = None,
) -> list[Path]:
    """Export one logical figure through ``pubify-mpl`` using framework-owned filenames."""

    if backend is None:
        backend = pubify_mpl

    if not mode_extension.startswith("."):
        raise ValueError(f"Invalid mode extension '{mode_extension}'")

    output_dir.mkdir(parents=True, exist_ok=True)
    layout = result.layout or config.pubify_mpl.default_layout

    panel_count = len(result.panels)
    if subfigure_index is not None:
        if subfigure_index < 1 or subfigure_index > panel_count:
            raise IndexError(
                f"Figure '{figure_id}' has {panel_count} panel(s); requested subfigure {subfigure_index}"
            )
        indices = [subfigure_index - 1]
    else:
        indices = list(range(panel_count))

    paths: list[Path] = []
    for idx in indices:
        current_panel = result.panels[idx]
        output_path = output_dir / output_filename(figure_id, panel_count, idx, mode_extension)
        pubify_kwargs = dict(config.pubify_mpl.defaults)
        pubify_kwargs.pop("layout", None)
        if result.caption_lines is not None:
            pubify_kwargs["caption_lines"] = result.caption_lines
        if result.subcaption_lines is not None:
            pubify_kwargs["subcaption_lines"] = result.subcaption_lines
        pubify_kwargs.update(result.kwargs)
        if current_panel.subcaption_lines is not None:
            pubify_kwargs["subcaption_lines"] = current_panel.subcaption_lines
        pubify_kwargs.update(current_panel.overrides)
        pubify_kwargs.setdefault("skip_clone", True)
        backend.save_fig(
            current_panel.figure,
            layout,
            output_path,
            template=config.pubify_mpl.template,
            **pubify_kwargs,
        )
        _close_export_source(current_panel.figure)
        paths.append(output_path)

    return paths


def save_pubify_figure(
    figure: object,
    *,
    layout: str,
    filename: str | Path,
    template: dict[str, object],
    prepare_root: Path,
    backend: object | None = None,
    **kwargs: object,
) -> None:
    """Export one figure directly through ``pubify-mpl`` with an absolute output path."""

    if backend is None:
        backend = pubify_mpl

    path = Path(filename).expanduser()
    if not path.is_absolute():
        raise ValueError("save_pubify_figure requires an absolute filename")
    path.parent.mkdir(parents=True, exist_ok=True)
    backend.prepare(prepare_root, template=template)
    kwargs.setdefault("skip_clone", True)
    backend.save_fig(figure, layout, path, template=template, **kwargs)


def output_filename(figure_id: str, count: int, idx: int, extension: str) -> str:
    """Return the framework-owned filename for one exported figure panel."""

    if count == 1:
        return f"{figure_id}{extension}"
    return f"{figure_id}_{idx + 1}{extension}"


def _is_pubify_export_target(value: object) -> bool:
    return isinstance(value, (Figure, Axes))


def _with_default_layout(result: FigureExport, config: PublicationConfig) -> FigureExport:
    if result.layout is not None:
        return result
    return FigureExport(
        panels=result.panels,
        layout=config.pubify_mpl.default_layout,
        caption_lines=result.caption_lines,
        subcaption_lines=result.subcaption_lines,
        kwargs=result.kwargs,
    )


def _close_export_source(value: object) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()
        return
    if isinstance(value, Figure):
        plt.close(value)
        return
    if isinstance(value, Axes):
        plt.close(value.figure)
