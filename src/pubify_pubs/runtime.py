from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import subprocess

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
    build_publication_paths,
    validate_publication_definition,
)
from pubify_pubs.export import export_figure, normalize_figure_result
from pubify_pubs.texlog import build_log_path, extract_latex_diagnostic

@dataclass
class RunContext:
    """Runtime state shared while resolving loaders and exporting figures."""

    publication: PublicationDefinition
    loader_cache: dict[str, object] = field(default_factory=dict)
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
    paths.publication_root.mkdir(parents=True, exist_ok=True)
    paths.data_root.mkdir(parents=True, exist_ok=True)
    if not paths.config_path.exists():
        write_skeleton_publication_config(paths.config_path, publication_id)
    if not paths.entrypoint.exists():
        write_skeleton_figures_module(paths.entrypoint)
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
) -> list[Path]:
    """Run one or more figures and export PDFs into ``tex/autofigures``."""

    errors = validate_publication_definition(publication, require_tex_support=False)
    if errors:
        joined = "\n".join(f"- {message}" for message in errors)
        raise ValueError(
            f"Publication '{publication.publication_id}' failed validation:\n{joined}"
        )
    ctx = RunContext(
        publication=publication,
        rc=PublicationRcContext(publication.config.pubify_mpl.template),
    )
    figure_ids = [figure_id] if figure_id is not None else sorted(publication.figures)
    outputs: list[Path] = []

    if figure_id is None:
        _clear_output_directory(publication.paths.autofigures_root)

    for current_id in figure_ids:
        if current_id not in publication.figures:
            raise KeyError(f"Unknown figure '{current_id}'")
        figure_outputs = _run_one_figure(
            ctx,
            publication.figures[current_id],
            subfigure_index,
        )
        outputs.extend(figure_outputs)

    return outputs


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
                f"Run `pubs {publication.publication_id} init` and try again."
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


CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


def build_pdf_path(publication: PublicationDefinition) -> Path:
    """Return the expected output PDF path under ``tex/build``."""

    return (
        publication.paths.build_root
        / publication.config.main_tex_path.with_suffix(".pdf").name
    )


def generated_exports_are_stale(publication: PublicationDefinition) -> bool:
    """Return whether a full figure export should run before build."""

    exports_root = publication.paths.autofigures_root
    if not exports_root.exists():
        return True

    exported_files = [path for path in exports_root.rglob("*") if path.is_file()]
    if not exported_files:
        return True

    newest_export_mtime = max(path.stat().st_mtime for path in exported_files)
    return publication.paths.entrypoint.stat().st_mtime > newest_export_mtime


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
        figure.func(ctx, *resolved_args),
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


def _clear_output_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _resolve_loader(ctx: RunContext, loader_id: str) -> object:
    loader = ctx.publication.loaders[loader_id]
    if not loader.nocache and loader_id in ctx.loader_cache:
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
        resolved = loader.func(ctx, _resolve_loader_path(root, relative_path, loader_id))
    elif loader.style == "named":
        resolved = loader.func(
            ctx,
            **{
                name: _resolve_loader_path(root, relative_path, loader_id)
                for name, relative_path in loader.relative_paths.items()
            },
        )
    else:
        raise ValueError(f"Unsupported loader style '{loader.style}'")

    if not loader.nocache:
        ctx.loader_cache[loader_id] = resolved
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
