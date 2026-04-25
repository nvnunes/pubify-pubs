from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
import io
import os
from pathlib import Path
import shutil
import subprocess
import traceback

import pubify_mpl
import pubify_data
import pubify_data.runtime as pubify_data_runtime
from pubify_mpl import pubify_rc_context as publication_rc_context

from pubify_pubs.config import (
    load_publication_config,
    load_workspace_config,
    write_publications_agents_file,
    write_skeleton_main_tex,
    write_skeleton_publication_config,
    write_skeleton_figures_module,
)
from pubify_pubs.discovery import (
    PublicationDefinition,
    PublicationPaths,
    StatSpec,
    TableSpec,
    build_publication_paths,
    validate_publication_definition,
)
from pubify_pubs.export import FigureResult, FigurePanel, export_figure, normalize_figure_result
from pubify_pubs.stats import (
    ComputedStat,
    compute_resolved_stat,
    ensure_unique_macro_names,
    render_autostats_text,
)
from pubify_pubs.tables import (
    ComputedTable,
    check_table_references,
    compute_table,
    render_autotables_text,
)
from pubify_pubs.texlog import build_log_path, extract_latex_diagnostic

@dataclass
class RunContext:
    """Runtime state shared while resolving loaders and exporting figures."""

    publication: PublicationDefinition
    loader_cache: dict[str, object] = field(default_factory=dict)
    command_loader_cache: dict[str, object] = field(default_factory=dict)
    updated_loader_ids: set[str] = field(default_factory=set)
    captured_data_output: dict[str, list[str]] = field(default_factory=dict)
    captured_output: dict[str, list[str]] = field(
        default_factory=lambda: {"figure": [], "stat": [], "table": []}
    )
    rc: AbstractContextManager[None] | None = None


class PublicationRcContext(AbstractContextManager[None]):
    """Reusable publication-bound Matplotlib rc context for figure construction."""

    def __init__(self, template: dict[str, object]) -> None:
        self._template = dict(template)
        self._active: AbstractContextManager[None] | None = None

    def __enter__(self) -> None:
        self._active = publication_rc_context(template=self._template)
        return self._active.__enter__()

    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:
        if self._active is None:
            return None
        active = self._active
        self._active = None
        return active.__exit__(exc_type, exc_value, traceback)


UserCodeExecutionError = pubify_data.UserCodeExecutionError


def check_publication(publication: PublicationDefinition) -> None:
    """Raise ``ValueError`` if the publication fails static validation."""

    errors = validate_publication_definition(publication, require_tex_support=True)
    if errors:
        joined = "\n".join(f"- {message}" for message in errors)
        raise ValueError(f"Publication '{publication.publication_id}' failed validation:\n{joined}")
    ensure_generated_artifact_paths(publication)
    check_tables(publication)


def init_publication(
    publication: PublicationDefinition,
    backend: object | None = None,
) -> tuple[Path, Path]:
    """Prepare the publication TeX tree and refresh package-owned support files."""

    if backend is None:
        backend = pubify_mpl

    publication.paths.tex_root.mkdir(parents=True, exist_ok=True)
    ensure_generated_artifact_paths(publication)
    publication.paths.build_root.mkdir(parents=True, exist_ok=True)
    return backend.prepare(
        publication.paths.tex_root,
        template=publication.config.pubify_mpl.template,
    )


def init_publication_by_id(
    workspace_root: Path,
    publication_id: str,
    backend: object | None = None,
    *,
    force: bool = False,
) -> Path:
    """Create any missing publication scaffolding, then prepare its TeX tree."""

    paths = build_publication_paths(workspace_root, publication_id)
    _ensure_publications_agents_file(workspace_root)
    wrote_figures_module = False
    paths.publication_root.mkdir(parents=True, exist_ok=True)
    paths.data_root.mkdir(parents=True, exist_ok=True)
    if not paths.config_path.exists():
        write_skeleton_publication_config(paths.config_path, publication_id)
    if not paths.entrypoint.exists():
        write_skeleton_figures_module(paths.entrypoint)
        wrote_figures_module = True
    if wrote_figures_module:
        example_data_path = paths.data_root / "path" / "to" / "file"
        example_data_path.parent.mkdir(parents=True, exist_ok=True)
        if not example_data_path.exists():
            example_data_path.write_text("", encoding="utf-8")
    config = load_publication_config(paths.config_path, publication_id)
    main_tex_path = paths.tex_root / config.main_tex_path
    if not main_tex_path.exists():
        write_skeleton_main_tex(main_tex_path)
    if backend is None:
        backend = pubify_mpl
    paths.tex_root.mkdir(parents=True, exist_ok=True)
    ensure_generated_artifact_paths(paths, force=force)
    paths.build_root.mkdir(parents=True, exist_ok=True)
    backend.prepare(paths.tex_root, template=config.pubify_mpl.template)
    return paths.publication_root


def ensure_generated_artifact_paths(
    publication_or_paths: PublicationDefinition | PublicationPaths,
    *,
    force: bool = False,
) -> None:
    """Create canonical generated-artifact paths and their local TeX symlink view."""

    paths = getattr(publication_or_paths, "paths", publication_or_paths)
    paths.data_root.mkdir(parents=True, exist_ok=True)
    paths.tex_artifacts_root.mkdir(parents=True, exist_ok=True)
    paths.autofigures_root.mkdir(parents=True, exist_ok=True)

    _ensure_artifact_symlink(paths.tex_autofigures_root, paths.autofigures_root, force=force)
    _ensure_artifact_symlink(paths.tex_autostats_path, paths.autostats_path, force=force)
    _ensure_artifact_symlink(paths.tex_autotables_path, paths.autotables_path, force=force)
    for output_file in (paths.autostats_path, paths.autotables_path):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if not output_file.exists():
            output_file.write_text("", encoding="utf-8")


def _ensure_artifact_symlink(link_path: Path, target_path: Path, *, force: bool) -> None:
    expected_target = _relative_symlink_target(link_path, target_path)
    if link_path.is_symlink():
        if Path(os.readlink(link_path)) == expected_target:
            return
        if not force:
            raise ValueError(
                f"Generated artifact link has unexpected target: {link_path}. "
                "Run `pubs --force init <publication-id>` to repair it."
            )
        link_path.unlink()
    elif link_path.exists():
        if not force:
            raise ValueError(
                f"Generated artifact path must be a symlink to {expected_target}: {link_path}. "
                "Run `pubs --force init <publication-id>` to migrate it."
            )
        _migrate_generated_artifact(link_path, target_path)

    link_path.parent.mkdir(parents=True, exist_ok=True)
    link_path.symlink_to(expected_target, target_is_directory=target_path.is_dir())


def _relative_symlink_target(link_path: Path, target_path: Path) -> Path:
    return Path(os.path.relpath(target_path, start=link_path.parent))


def _migrate_generated_artifact(source: Path, destination: Path) -> None:
    if source.is_dir():
        _merge_generated_artifact_directory(source, destination)
        shutil.rmtree(source)
        return
    if destination.exists() and destination.read_bytes() != source.read_bytes():
        raise ValueError(f"Generated artifact migration conflict: {source} -> {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.move(str(source), str(destination))
    else:
        source.unlink()


def _merge_generated_artifact_directory(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            _merge_generated_artifact_directory(child, target)
            continue
        if target.exists() and target.read_bytes() != child.read_bytes():
            raise ValueError(f"Generated artifact migration conflict: {child} -> {target}")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(target))


def ensure_publications_agents_file(workspace_root: Path) -> Path:
    """Create the shared publications-root ``AGENTS.md`` when missing."""

    return _ensure_publications_agents_file(workspace_root)


def _ensure_publications_agents_file(workspace_root: Path) -> Path:
    workspace = load_workspace_config(workspace_root)
    agents_path = workspace.publications_root / "AGENTS.md"
    if not agents_path.exists():
        write_publications_agents_file(agents_path)
    return agents_path


def run_figures(
    publication: PublicationDefinition,
    figure_id: str | None = None,
    subfigure_index: int | None = None,
    ctx: RunContext | None = None,
) -> list[Path]:
    """Run one or more figures and export PDFs into the generated artifact store."""

    errors = validate_publication_definition(publication, require_tex_support=False)
    if errors:
        joined = "\n".join(f"- {message}" for message in errors)
        raise ValueError(
            f"Publication '{publication.publication_id}' failed validation:\n{joined}"
        )
    ensure_generated_artifact_paths(publication)
    run_ctx = ctx or build_run_context(publication)
    available_figure_ids = set(pubify_data.figure_ids(publication.upstream))
    figure_ids = [figure_id] if figure_id is not None else sorted(available_figure_ids)
    outputs: list[Path] = []

    if figure_id is None:
        _clear_output_directory(publication.paths.autofigures_root)

    for current_id in figure_ids:
        if current_id not in available_figure_ids:
            raise KeyError(f"Unknown figure '{current_id}'")
        figure_outputs = _run_one_figure(
            run_ctx,
            current_id,
            subfigure_index,
        )
        outputs.extend(figure_outputs)

    return outputs


def inspect_figure(
    publication: PublicationDefinition,
    figure_id: str,
    ctx: RunContext | None = None,
) -> FigureResult:
    """Resolve one figure to its normalized ``FigureResult`` without exporting files."""

    errors = validate_publication_definition(publication, require_tex_support=False)
    if errors:
        joined = "\n".join(f"- {message}" for message in errors)
        raise ValueError(
            f"Publication '{publication.publication_id}' failed validation:\n{joined}"
        )
    if figure_id not in pubify_data.figure_ids(publication.upstream):
        raise KeyError(f"Unknown figure '{figure_id}'")
    run_ctx = ctx or build_run_context(publication)
    _, result = pubify_data_runtime.run_figures(
        publication.upstream,
        figure_id,
        ctx=_upstream_context(run_ctx),
    )[0]
    return _figure_result_from_neutral(result, publication.config)


def run_stats(
    publication: PublicationDefinition,
    stat_id: str | None = None,
    ctx: RunContext | None = None,
) -> tuple[ComputedStat, ...]:
    """Run one or more stats and return normalized computed stat values."""

    errors = validate_publication_definition(publication, require_tex_support=False)
    if errors:
        joined = "\n".join(f"- {message}" for message in errors)
        raise ValueError(
            f"Publication '{publication.publication_id}' failed validation:\n{joined}"
        )
    run_ctx = ctx or build_run_context(publication)
    neutral_stats = pubify_data_runtime.run_stats(
        publication.upstream,
        stat_id,
        ctx=_upstream_context(run_ctx),
    )
    computed = [compute_resolved_stat(stat.stat_id, stat) for stat in neutral_stats]
    result = tuple(computed)
    ensure_unique_macro_names(result)
    return result


def update_stats(
    publication: PublicationDefinition,
    ctx: RunContext | None = None,
) -> tuple[Path, tuple[ComputedStat, ...]]:
    """Compute all stats and rewrite the framework-owned ``autostats.tex`` snapshot."""

    ensure_generated_artifact_paths(publication)
    computed = run_stats(publication, ctx=ctx)
    output_path = publication.paths.autostats_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_autostats_text(computed), encoding="utf-8")
    return output_path, computed


def run_tables(
    publication: PublicationDefinition,
    table_id: str | None = None,
    ctx: RunContext | None = None,
) -> tuple[ComputedTable, ...]:
    """Run one or more tables and return normalized computed table bodies."""

    errors = validate_publication_definition(publication, require_tex_support=False)
    if errors:
        joined = "\n".join(f"- {message}" for message in errors)
        raise ValueError(
            f"Publication '{publication.publication_id}' failed validation:\n{joined}"
        )
    run_ctx = ctx or build_run_context(publication)
    neutral_tables = pubify_data_runtime.run_tables(
        publication.upstream,
        table_id,
        ctx=_upstream_context(run_ctx),
    )
    computed = [compute_table(table.table_id, table) for table in neutral_tables]
    return tuple(computed)


def update_tables(
    publication: PublicationDefinition,
    ctx: RunContext | None = None,
) -> tuple[Path, tuple[ComputedTable, ...]]:
    """Compute all tables and rewrite the framework-owned ``autotables.tex`` snapshot."""

    ensure_generated_artifact_paths(publication)
    computed = run_tables(publication, ctx=ctx)
    output_path = publication.paths.autotables_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_autotables_text(computed), encoding="utf-8")
    return output_path, computed


def write_computed_tables(
    publication: PublicationDefinition,
    computed: tuple[ComputedTable, ...],
) -> Path:
    """Write an already-computed table snapshot to ``autotables.tex``."""

    ensure_generated_artifact_paths(publication)
    output_path = publication.paths.autotables_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_autotables_text(computed), encoding="utf-8")
    return output_path


def check_tables(
    publication: PublicationDefinition,
    table_id: str | None = None,
    ctx: RunContext | None = None,
) -> None:
    """Validate discovered tables against surrounding manuscript table definitions."""

    ensure_generated_artifact_paths(publication)
    computed = run_tables(publication, table_id=table_id, ctx=ctx)
    check_table_references(
        publication.paths.tex_root,
        publication.config.main_tex_path,
        computed,
        table_id=table_id,
    )


def write_computed_stats(
    publication: PublicationDefinition,
    computed: tuple[ComputedStat, ...],
) -> Path:
    """Write an already-computed stat snapshot to ``autostats.tex``."""

    ensure_unique_macro_names(computed)
    ensure_generated_artifact_paths(publication)
    output_path = publication.paths.autostats_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_autostats_text(computed), encoding="utf-8")
    return output_path


def build_run_context(
    publication: PublicationDefinition,
    *,
    loader_cache: dict[str, object] | None = None,
) -> RunContext:
    """Create one command-scoped runtime context for a publication."""

    return RunContext(
        publication=publication,
        loader_cache=loader_cache if loader_cache is not None else {},
        rc=PublicationRcContext(publication.config.pubify_mpl.template),
    )


def _upstream_context(ctx: RunContext) -> pubify_data.RunContext:
    upstream = pubify_data.build_run_context(
        ctx.publication.upstream,
        loader_cache=ctx.loader_cache,
        rc=ctx.rc,
    )
    upstream.command_loader_cache = ctx.command_loader_cache
    upstream.updated_loader_ids = ctx.updated_loader_ids
    upstream.captured_data_output = ctx.captured_data_output
    upstream.captured_output = ctx.captured_output
    return upstream


def preload_loaders(
    ctx: RunContext,
    loader_ids: tuple[str, ...],
    *,
    include_nocache: bool,
) -> None:
    """Resolve selected loaders into ``ctx`` before figures or stats run."""

    for loader_id in loader_ids:
        loader = ctx.publication.loaders[loader_id]
        if loader.nocache and not include_nocache:
            continue
        pubify_data_runtime.resolve_loader(_upstream_context(ctx), loader_id)


def resolve_loader(ctx: RunContext, loader_id: str) -> object:
    """Resolve one loader and return its computed value."""

    return pubify_data_runtime.resolve_loader(_upstream_context(ctx), loader_id)


def build_publication(
    publication: PublicationDefinition,
    runner: CommandRunner | None = None,
) -> subprocess.CompletedProcess[str]:
    """Compile the publication's TeX source into ``tex/build`` with ``latexmk``."""

    ensure_generated_artifact_paths(publication)
    errors = validate_publication_definition(publication, require_tex_support=True)
    if errors:
        missing_support = [
            message for message in errors if "Missing pubify support file:" in message
        ]
        if missing_support:
            raise ValueError(
                f"Publication '{publication.publication_id}' is not initialized for LaTeX build. "
                f"Run `pubs init {publication.publication_id}` and try again."
            )
        joined = "\n".join(f"- {message}" for message in errors)
        raise ValueError(
            f"Publication '{publication.publication_id}' failed validation:\n{joined}"
        )
    publication.paths.build_root.mkdir(parents=True, exist_ok=True)
    main_tex = publication.config.main_tex_path.as_posix()
    command = [
        "latexmk",
        "-pdf",
        "-interaction=nonstopmode",
        "-halt-on-error",
        f"-outdir={publication.paths.build_root}",
        main_tex,
    ]
    command_runner = runner or run_command
    try:
        return command_runner(command, cwd=publication.paths.tex_root)
    except subprocess.CalledProcessError as exc:
        if _is_stale_latexmk_failure(exc):
            forced_command = list(command)
            forced_command.insert(1, "-g")
            try:
                return command_runner(forced_command, cwd=publication.paths.tex_root)
            except subprocess.CalledProcessError as forced_exc:
                raise ValueError(
                    _format_latex_build_failure(publication, forced_exc.returncode)
                ) from None
        raise ValueError(_format_latex_build_failure(publication, exc.returncode)) from None


def clear_publication_build(publication: PublicationDefinition) -> None:
    """Remove all files from ``tex/build`` so the next build starts fresh."""

    _clear_output_directory(publication.paths.build_root)


def clear_autofigures(publication: PublicationDefinition) -> None:
    """Remove all generated figure files under the canonical generated artifact store."""

    ensure_generated_artifact_paths(publication)
    _clear_output_directory(publication.paths.autofigures_root)


CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


def build_pdf_path(publication: PublicationDefinition) -> Path:
    """Return the expected output PDF path under ``tex/build``."""

    return (
        publication.paths.build_root
        / publication.config.main_tex_path.with_suffix(".pdf").name
    )


def generated_outputs_are_stale(publication: PublicationDefinition) -> bool:
    """Return whether generated figure/stat outputs should refresh before build."""

    entrypoint_mtime = publication.paths.entrypoint.stat().st_mtime

    if publication.figures:
        exports_root = publication.paths.autofigures_root
        if not exports_root.exists():
            return True
        exported_files = [path for path in exports_root.rglob("*") if path.is_file()]
        if not exported_files:
            return True
        newest_export_mtime = max(path.stat().st_mtime for path in exported_files)
        if entrypoint_mtime > newest_export_mtime:
            return True

    if publication.stats:
        autostats = publication.paths.autostats_path
        if not autostats.exists():
            return True
        if entrypoint_mtime > autostats.stat().st_mtime:
            return True

    if publication.tables:
        autotables = publication.paths.autotables_path
        if not autotables.exists():
            return True
        if entrypoint_mtime > autotables.stat().st_mtime:
            return True

    return False


def run_command(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one subprocess command in ``cwd`` and raise on failure."""

    return subprocess.run(
        list(command),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _format_latex_build_failure(publication: PublicationDefinition, exit_code: int) -> str:
    log_path = build_log_path(publication.paths.build_root, publication.config.main_tex_path)
    diagnostic = extract_latex_diagnostic(log_path)
    lines = [
        f"LaTeX build failed for '{publication.publication_id}' (latexmk exit {exit_code}).",
        f"Log file: {log_path}",
    ]
    if diagnostic is None:
        lines.append("LaTeX error: no LaTeX diagnostic could be extracted.")
        return "\n".join(lines)
    lines.append(f"LaTeX error: {diagnostic.summary}")
    if diagnostic.source is not None:
        lines.append(f"Source: {diagnostic.source}")
    if diagnostic.context is not None:
        lines.append(f"Context: {diagnostic.context}")
    return "\n".join(lines)


def _is_stale_latexmk_failure(exc: subprocess.CalledProcessError) -> bool:
    output = "\n".join(part for part in (exc.stdout, exc.stderr) if part)
    return (
        exc.returncode == 12
        and "Nothing to do for" in output
        and "gave an error in previous invocation of latexmk" in output
    )


def _run_one_figure(
    ctx: RunContext,
    figure_id: str,
    subfigure_index: int | None,
) -> list[Path]:
    _, neutral_result = pubify_data_runtime.run_figures(
        ctx.publication.upstream,
        figure_id,
        ctx=_upstream_context(ctx),
    )[0]
    result = _figure_result_from_neutral(neutral_result, ctx.publication.config)
    return _capture_export_output(ctx, figure_id, result, subfigure_index)


def _figure_result_from_neutral(
    result: pubify_data.BaseFigureResult,
    config: object,
) -> FigureResult:
    if isinstance(result, FigureResult):
        return normalize_figure_result(result, config)
    panels: list[FigurePanel] = []
    for panel in result.panels:
        metadata = dict(panel.metadata)
        subcaption_lines = metadata.pop("subcaption_lines", None)
        panels.append(
            FigurePanel(
                panel.payload,
                subcaption_lines=subcaption_lines,
                overrides=metadata,
            )
        )
    metadata = dict(result.metadata)
    caption_lines = metadata.pop("caption_lines", None)
    subcaption_lines = metadata.pop("subcaption_lines", None)
    return normalize_figure_result(
        FigureResult(
            panels,
            layout=result.layout,
            caption_lines=caption_lines,
            subcaption_lines=subcaption_lines,
            kwargs=metadata,
        ),
        config,
    )


def _capture_export_output(
    ctx: RunContext,
    figure_id: str,
    result: FigureResult,
    subfigure_index: int | None,
) -> list[Path]:
    stream = io.StringIO()
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            exported = export_figure(
                ctx.publication.config,
                ctx.publication.paths.tex_root,
                ctx.publication.paths.autofigures_root,
                figure_id,
                result,
                ".pdf",
                subfigure_index=subfigure_index,
            )
    except Exception as exc:
        lines = stream.getvalue().splitlines()
        lines.extend(
            line.rstrip("\n")
            for line in traceback.format_exception_only(type(exc), exc)
            if line.rstrip("\n")
        )
        raise UserCodeExecutionError(lines) from exc
    output = stream.getvalue()
    if output:
        ctx.captured_output["figure"].extend(output.splitlines())
    return exported


def _run_one_stat(ctx: RunContext, stat: StatSpec) -> ComputedStat:
    neutral_stat = pubify_data_runtime.run_stats(
        ctx.publication.upstream,
        stat.stat_id,
        ctx=_upstream_context(ctx),
    )[0]
    return compute_resolved_stat(neutral_stat.stat_id, neutral_stat)


def _run_one_table(ctx: RunContext, table: TableSpec) -> ComputedTable:
    neutral_table = pubify_data_runtime.run_tables(
        ctx.publication.upstream,
        table.table_id,
        ctx=_upstream_context(ctx),
    )[0]
    return compute_table(neutral_table.table_id, neutral_table)


def _clear_output_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _resolve_loader(ctx: RunContext, loader_id: str) -> object:
    return pubify_data_runtime.resolve_loader(_upstream_context(ctx), loader_id)
