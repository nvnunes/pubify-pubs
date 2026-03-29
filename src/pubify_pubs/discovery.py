from __future__ import annotations

from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
import inspect
from pathlib import Path
import sys
from types import ModuleType

from pubify_pubs.config import (
    PublicationConfig,
    find_workspace_root as resolve_workspace_root,
    load_publication_config,
    load_workspace_config,
)


PUBLICATION_ENTRYPOINT = "figures.py"
PUBLICATION_CONFIG = "pub.yaml"
PUBIFY_STYLE = "pubify.sty"
PUBIFY_TEMPLATE = "pubify-template.tex"


@dataclass(frozen=True)
class LoaderSpec:
    """Discovered loader metadata derived from one decorated loader function."""

    loader_id: str
    func: object
    kind: str
    root_name: str | None
    style: str
    relative_paths: dict[str, str]
    nocache: bool


@dataclass(frozen=True)
class FigureSpec:
    """Discovered figure metadata derived from one decorated figure function."""

    figure_id: str
    func: object
    dependency_ids: tuple[str, ...]


@dataclass(frozen=True)
class PublicationPaths:
    """Resolved workspace and publication paths used by the runtime."""

    workspace_root: Path
    publication_root: Path
    data_root: Path
    tex_root: Path
    sync_base_root: Path
    build_root: Path
    autofigures_root: Path
    entrypoint: Path
    config_path: Path


@dataclass(frozen=True)
class PublicationDefinition:
    """Loaded publication module plus its resolved config, paths, and decorators."""

    publication_id: str
    paths: PublicationPaths
    config: PublicationConfig
    module: ModuleType
    loaders: dict[str, LoaderSpec]
    figures: dict[str, FigureSpec]


def find_workspace_root(start: Path | None = None) -> Path:
    """Walk upward from ``start`` until a publication workspace root is found."""

    return resolve_workspace_root(start)


def list_publication_ids(workspace_root: Path) -> list[str]:
    """List publication ids under the configured workspace publications root."""

    publications_root = load_workspace_config(workspace_root).publications_root
    if not publications_root.exists():
        return []
    return sorted(path.name for path in publications_root.iterdir() if path.is_dir())


def load_publication_definition(workspace_root: Path, publication_id: str) -> PublicationDefinition:
    """Load one publication's config, entrypoint module, loaders, and figures."""

    paths = build_publication_paths(workspace_root, publication_id)
    if not paths.publication_root.exists():
        raise FileNotFoundError(f"Unknown publication '{publication_id}'")
    if not paths.entrypoint.exists():
        raise FileNotFoundError(f"Missing figures entrypoint: {paths.entrypoint}")
    if not paths.config_path.exists():
        raise FileNotFoundError(f"Missing publication config: {paths.config_path}")

    config = load_publication_config(paths.config_path, publication_id)
    module = _import_publication_module(publication_id, paths.entrypoint)
    loaders = _discover_loaders(module)
    figures = _discover_figures(module)
    return PublicationDefinition(
        publication_id=publication_id,
        paths=paths,
        config=config,
        module=module,
        loaders=loaders,
        figures=figures,
    )


def validate_publication_definition(
    publication: PublicationDefinition,
    *,
    require_tex_support: bool,
) -> list[str]:
    """Return static validation errors without running loaders, figures, or LaTeX."""

    errors: list[str] = []

    for loader in publication.loaders.values():
        for relative_path in loader.relative_paths.values():
            if loader.kind == "data":
                data_path = publication.paths.data_root / relative_path
                if not data_path.exists():
                    errors.append(f"Missing data path for loader '{loader.loader_id}': {data_path}")
                continue

            if loader.kind == "external_data":
                if (
                    loader.root_name is None
                    or loader.root_name not in publication.config.external_data_roots
                ):
                    errors.append(
                        "Missing external data root config for loader "
                        f"'{loader.loader_id}': {loader.root_name}"
                    )
                    continue
                root_path = Path(
                    publication.config.external_data_roots[loader.root_name]
                ).expanduser()
                if not root_path.exists():
                    errors.append(
                        f"Missing external data root path for loader '{loader.loader_id}': {root_path}"
                    )
                    continue
                data_path = root_path / relative_path
                if not data_path.exists():
                    errors.append(
                        f"Missing external data path for loader '{loader.loader_id}': {data_path}"
                    )
                continue

            errors.append(f"Unsupported loader kind for loader '{loader.loader_id}': {loader.kind}")

    for figure in publication.figures.values():
        for dep in figure.dependency_ids:
            if dep not in publication.loaders:
                errors.append(
                    f"Figure '{figure.figure_id}' depends on unknown loader '{dep}'"
                )

    if not publication.paths.tex_root.exists():
        errors.append(f"Missing tex directory: {publication.paths.tex_root}")

    main_tex = publication.paths.tex_root / publication.config.main_tex_path
    if not main_tex.exists():
        errors.append(f"Missing main tex file: {main_tex}")

    mirror_root = publication.config.mirror_root_path
    if mirror_root is not None and not mirror_root.exists():
        errors.append(f"Mirror does not exist: {mirror_root}")

    if require_tex_support:
        style_path = publication.paths.tex_root / PUBIFY_STYLE
        if not style_path.exists():
            errors.append(f"Missing pubify support file: {style_path}")
        template_path = publication.paths.tex_root / PUBIFY_TEMPLATE
        if not template_path.exists():
            errors.append(f"Missing pubify support file: {template_path}")

    return errors


def build_publication_paths(workspace_root: Path, publication_id: str) -> PublicationPaths:
    """Resolve all framework-owned paths for one publication under a workspace."""

    workspace = load_workspace_config(workspace_root)
    publication_root = workspace.publications_root / publication_id
    tex_root = publication_root / "tex"
    return PublicationPaths(
        workspace_root=workspace_root,
        publication_root=publication_root,
        data_root=workspace.data_root / publication_id,
        tex_root=tex_root,
        sync_base_root=tex_root / ".pubs-sync-base",
        build_root=tex_root / "build",
        autofigures_root=tex_root / "autofigures",
        entrypoint=publication_root / PUBLICATION_ENTRYPOINT,
        config_path=publication_root / PUBLICATION_CONFIG,
    )


def _import_publication_module(publication_id: str, entrypoint: Path) -> ModuleType:
    module_name = f"pubify_pubs_publication_{publication_id}"
    spec = spec_from_file_location(module_name, entrypoint)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not build import spec for {entrypoint}")
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _discover_loaders(module: ModuleType) -> dict[str, LoaderSpec]:
    loaders: dict[str, LoaderSpec] = {}
    for _, member in module.__dict__.items():
        metadata = getattr(member, "__pubs_loader__", None)
        if metadata is None:
            continue
        loader_id = _strip_prefix(member.__name__, "load_")
        if loader_id in loaders:
            raise ValueError(f"Duplicate loader id '{loader_id}'")
        _validate_loader_signature(loader_id, member, metadata["style"], dict(metadata["paths"]))
        loaders[loader_id] = LoaderSpec(
            loader_id=loader_id,
            func=member,
            kind=metadata["kind"],
            root_name=metadata.get("root_name"),
            style=metadata["style"],
            relative_paths=dict(metadata["paths"]),
            nocache=bool(metadata["nocache"]),
        )
    return loaders


def _discover_figures(module: ModuleType) -> dict[str, FigureSpec]:
    figures: dict[str, FigureSpec] = {}
    for _, member in inspect.getmembers(module):
        if not getattr(member, "__pubs_figure__", False):
            continue
        figure_id = _strip_prefix(member.__name__, "plot_")
        if figure_id in figures:
            raise ValueError(f"Duplicate figure id '{figure_id}'")
        figures[figure_id] = FigureSpec(
            figure_id=figure_id,
            func=member,
            dependency_ids=_figure_dependency_ids(member),
        )
    return figures


def _validate_loader_signature(
    loader_id: str,
    func: object,
    style: str,
    paths: dict[str, str],
) -> None:
    params = tuple(inspect.signature(func).parameters.values())
    if not params or params[0].name != "ctx":
        raise ValueError(f"Loader '{func.__name__}' must accept ctx as its first parameter")

    resolved_params = params[1:]
    if style == "single":
        if len(resolved_params) != 1:
            raise ValueError(
                f"Loader '{loader_id}' must accept exactly one resolved path parameter after ctx"
            )
        return

    if style == "named":
        expected_names = tuple(paths)
        param_names = tuple(param.name for param in resolved_params)
        if param_names != expected_names:
            raise ValueError(
                f"Loader '{loader_id}' must accept named path parameters {expected_names} after ctx"
            )
        return

    raise ValueError(f"Unsupported loader style for loader '{loader_id}': {style}")


def _figure_dependency_ids(func: object) -> tuple[str, ...]:
    params = tuple(inspect.signature(func).parameters.values())
    if not params or params[0].name != "ctx":
        raise ValueError(f"Figure '{func.__name__}' must accept ctx as its first parameter")
    return tuple(param.name for param in params[1:])


def _strip_prefix(name: str, prefix: str) -> str:
    return name[len(prefix) :] if name.startswith(prefix) else name
