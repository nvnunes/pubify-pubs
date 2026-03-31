from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
import io
from pathlib import Path
import shutil
import subprocess
import traceback

import pubify_mpl
from pubify_mpl import pubify_rc_context as publication_rc_context

from pubify_pubs.config import (
    load_publication_config,
    write_skeleton_main_tex,
    write_skeleton_publication_config,
    write_skeleton_figures_module,
)
from pubify_pubs.discovery import (
    FigureSpec,
    PublicationDefinition,
    StatSpec,
    build_publication_paths,
    validate_publication_definition,
)
from pubify_pubs.export import export_figure, normalize_figure_result
from pubify_pubs.stats import (
    ComputedStat,
    autostats_path,
    compute_resolved_stat,
    ensure_unique_macro_names,
    render_autostats_text,
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
        default_factory=lambda: {"figure": [], "stat": []}
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


class UserCodeExecutionError(RuntimeError):
    """Raised when publication-defined Python code fails during execution."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = tuple(lines)
        super().__init__(lines[-1] if lines else "Publication code execution failed")


def check_publication(publication: PublicationDefinition) -> None:
    """Raise ``ValueError`` if the publication fails static validation."""

    errors = validate_publication_definition(publication, require_tex_support=True)
    if errors:
        joined = "\n".join(f"- {message}" for message in errors)
        raise ValueError(f"Publication '{publication.publication_id}' failed validation:\n{joined}")


def init_publication(
    publication: PublicationDefinition,
    backend: object | None = None,
) -> tuple[Path, Path]:
    """Prepare the publication TeX tree and refresh package-owned support files."""

    if backend is None:
        backend = pubify_mpl

    publication.paths.tex_root.mkdir(parents=True, exist_ok=True)
    publication.paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    publication.paths.build_root.mkdir(parents=True, exist_ok=True)
    return backend.prepare(
        publication.paths.tex_root,
        template=publication.config.pubify_mpl.template,
    )


def init_publication_by_id(workspace_root: Path, publication_id: str, backend: object | None = None) -> Path:
    """Create any missing publication scaffolding, then prepare its TeX tree."""

    paths = build_publication_paths(workspace_root, publication_id)
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
    paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    paths.build_root.mkdir(parents=True, exist_ok=True)
    backend.prepare(paths.tex_root, template=config.pubify_mpl.template)
    return paths.publication_root


def run_figures(
    publication: PublicationDefinition,
    figure_id: str | None = None,
    subfigure_index: int | None = None,
    ctx: RunContext | None = None,
) -> list[Path]:
    """Run one or more figures and export PDFs into ``tex/autofigures``."""

    errors = validate_publication_definition(publication, require_tex_support=False)
    if errors:
        joined = "\n".join(f"- {message}" for message in errors)
        raise ValueError(
            f"Publication '{publication.publication_id}' failed validation:\n{joined}"
        )
    run_ctx = ctx or build_run_context(publication)
    figure_ids = [figure_id] if figure_id is not None else sorted(publication.figures)
    outputs: list[Path] = []

    if figure_id is None:
        _clear_output_directory(publication.paths.autofigures_root)

    for current_id in figure_ids:
        if current_id not in publication.figures:
            raise KeyError(f"Unknown figure '{current_id}'")
        figure_outputs = _run_one_figure(
            run_ctx,
            publication.figures[current_id],
            subfigure_index,
        )
        outputs.extend(figure_outputs)

    return outputs


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
    stat_ids = [stat_id] if stat_id is not None else sorted(publication.stats)
    computed: list[ComputedStat] = []
    for current_id in stat_ids:
        if current_id not in publication.stats:
            raise KeyError(f"Unknown stat '{current_id}'")
        computed.append(_run_one_stat(run_ctx, publication.stats[current_id]))
    result = tuple(computed)
    ensure_unique_macro_names(result)
    return result


def update_stats(
    publication: PublicationDefinition,
    ctx: RunContext | None = None,
) -> tuple[Path, tuple[ComputedStat, ...]]:
    """Compute all stats and rewrite the framework-owned ``autostats.tex`` snapshot."""

    computed = run_stats(publication, ctx=ctx)
    output_path = autostats_path(publication.paths.tex_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_autostats_text(computed), encoding="utf-8")
    return output_path, computed


def write_computed_stats(
    publication: PublicationDefinition,
    computed: tuple[ComputedStat, ...],
) -> Path:
    """Write an already-computed stat snapshot to ``autostats.tex``."""

    ensure_unique_macro_names(computed)
    output_path = autostats_path(publication.paths.tex_root)
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
        _resolve_loader(ctx, loader_id)


def resolve_loader(ctx: RunContext, loader_id: str) -> object:
    """Resolve one loader and return its computed value."""

    return _resolve_loader(ctx, loader_id)


def build_publication(
    publication: PublicationDefinition,
    runner: CommandRunner | None = None,
) -> subprocess.CompletedProcess[str]:
    """Compile the publication's TeX source into ``tex/build`` with ``latexmk``."""

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
    """Remove all generated figure files under ``tex/autofigures``."""

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
    figure: FigureSpec,
    subfigure_index: int | None,
) -> list[Path]:
    resolved_args = [_resolve_loader(ctx, dep_id) for dep_id in figure.dependency_ids]
    output_dir = ctx.publication.paths.autofigures_root
    result = normalize_figure_result(
        _capture_dynamic_output(ctx, "figure", figure.func, ctx, *resolved_args),
        ctx.publication.config,
    )
    return export_figure(
        ctx.publication.config,
        ctx.publication.paths.tex_root,
        output_dir,
        figure.figure_id,
        result,
        ".pdf",
        subfigure_index=subfigure_index,
    )


def _run_one_stat(ctx: RunContext, stat: StatSpec) -> ComputedStat:
    resolved_args = [_resolve_loader(ctx, dep_id) for dep_id in stat.dependency_ids]
    return compute_resolved_stat(
        stat.stat_id,
        _capture_dynamic_output(ctx, "stat", stat.func, ctx, *resolved_args),
    )


def _clear_output_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _resolve_loader(ctx: RunContext, loader_id: str) -> object:
    loader = ctx.publication.loaders[loader_id]
    if loader.nocache:
        if loader_id in ctx.command_loader_cache:
            return ctx.command_loader_cache[loader_id]
    elif loader_id in ctx.loader_cache:
        return ctx.loader_cache[loader_id]

    if loader.kind == "data":
        root = ctx.publication.paths.data_root
    elif loader.kind == "external_data":
        if loader.root_name is None:
            raise ValueError(f"Loader '{loader_id}' is missing external data root metadata")
        if loader.root_name not in ctx.publication.config.external_data_roots:
            raise ValueError(
                f"Loader '{loader_id}' references undefined external data root "
                f"'{loader.root_name}'"
            )
        root = Path(ctx.publication.config.external_data_roots[loader.root_name]).expanduser()
    else:
        raise ValueError(f"Unsupported loader kind '{loader.kind}'")

    if loader.style == "single":
        relative_path = next(iter(loader.relative_paths.values()))
        resolved = _capture_loader_output(
            ctx,
            loader_id,
            loader.func,
            ctx,
            _resolve_loader_path(root, relative_path, loader_id),
        )
    elif loader.style == "named":
        resolved = _capture_loader_output(
            ctx,
            loader_id,
            loader.func,
            ctx,
            **{
                name: _resolve_loader_path(root, relative_path, loader_id)
                for name, relative_path in loader.relative_paths.items()
            },
        )
    else:
        raise ValueError(f"Unsupported loader style '{loader.style}'")

    if loader.nocache:
        ctx.command_loader_cache[loader_id] = resolved
    else:
        ctx.loader_cache[loader_id] = resolved
    ctx.updated_loader_ids.add(loader_id)
    return resolved


def _resolve_loader_path(root: Path, relative_path: str, loader_id: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(f"Loader '{loader_id}' path must be relative, not absolute: {relative_path}")
    if any(part == ".." for part in candidate.parts):
        raise ValueError(
            f"Loader '{loader_id}' path must stay under its configured root: {relative_path}"
        )
    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        raise ValueError(f"Loader '{loader_id}' path must be a non-empty relative path")
    return root / candidate


def _capture_dynamic_output(
    ctx: RunContext,
    group: str,
    func: Callable[..., object],
    *args: object,
    **kwargs: object,
) -> object:
    stream = io.StringIO()
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            result = func(*args, **kwargs)
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
        ctx.captured_output[group].extend(output.splitlines())
    return result


def _capture_loader_output(
    ctx: RunContext,
    loader_id: str,
    func: Callable[..., object],
    *args: object,
    **kwargs: object,
) -> object:
    stream = io.StringIO()
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            result = func(*args, **kwargs)
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
        ctx.captured_data_output.setdefault(loader_id, []).extend(output.splitlines())
    return result
