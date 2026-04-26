from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pubify_data

from pubify_pubs.config import (
    PublicationConfig,
    find_workspace_root as resolve_workspace_root,
    load_publication_config,
    load_workspace_config,
    resolve_publication_data_root,
)


PUBLICATION_ENTRYPOINT = "figures.py"
PUBLICATION_CONFIG = "pub.yaml"
PUBIFY_STYLE = "pubify.sty"
PUBIFY_TEMPLATE = "pubify-template.tex"
TEX_ARTIFACTS_NAMESPACE = "tex-artifacts"

LoaderSpec = pubify_data.LoaderSpec
FigureSpec = pubify_data.FigureSpec
StatSpec = pubify_data.StatSpec
TableSpec = pubify_data.TableSpec


@dataclass(frozen=True)
class PublicationPaths:
    """Resolved workspace and publication paths used by the runtime."""

    workspace_root: Path
    publication_root: Path
    data_root: Path
    tex_root: Path
    sync_base_root: Path
    build_root: Path
    versions_root: Path
    versions_metadata_path: Path
    tex_artifacts_root: Path
    autofigures_root: Path
    autostats_path: Path
    autotables_path: Path
    tex_autofigures_root: Path
    tex_autostats_path: Path
    tex_autotables_path: Path
    entrypoint: Path
    config_path: Path


@dataclass(frozen=True)
class PublicationDefinition:
    """Loaded publication module plus its resolved config, paths, and decorators."""

    publication_id: str
    paths: PublicationPaths
    config: PublicationConfig
    upstream: pubify_data.PublicationDefinition

    @property
    def module(self) -> ModuleType:
        return self.upstream.module

    @property
    def loaders(self) -> dict[str, LoaderSpec]:
        return self.upstream.loaders

    @property
    def figures(self) -> dict[str, FigureSpec]:
        return self.upstream.figures

    @property
    def stats(self) -> dict[str, StatSpec]:
        return self.upstream.stats

    @property
    def tables(self) -> dict[str, TableSpec]:
        return self.upstream.tables


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
    adapter = pubify_data.PublicationAdapter(
        publication_id=publication_id,
        publication_root=paths.publication_root,
        entrypoint=paths.entrypoint,
        data_root=paths.data_root,
        external_data_roots=config.external_data_roots,
        source_adapters=_build_source_adapters(paths.workspace_root, config.sources),
        workspace=pubify_data.WorkspaceAdapter(paths.workspace_root),
    )
    upstream = pubify_data.load_publication_from_entrypoint(publication_id, adapter=adapter)
    return PublicationDefinition(
        publication_id=publication_id,
        paths=paths,
        config=config,
        upstream=upstream,
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

    errors.extend(pubify_data.validate_dependencies(publication.upstream))

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
    data_root = resolve_publication_data_root(workspace, publication_id)
    tex_artifacts_root = pubify_data.artifact_namespace_root(data_root, TEX_ARTIFACTS_NAMESPACE)
    return PublicationPaths(
        workspace_root=workspace_root,
        publication_root=publication_root,
        data_root=data_root,
        tex_root=tex_root,
        sync_base_root=tex_root / ".pubs-sync-base",
        build_root=tex_root / "build",
        versions_root=tex_root / "versions",
        versions_metadata_path=tex_root / "versions" / "metadata.yaml",
        tex_artifacts_root=tex_artifacts_root,
        autofigures_root=tex_artifacts_root / "autofigures",
        autostats_path=tex_artifacts_root / "autostats.tex",
        autotables_path=tex_artifacts_root / "autotables.tex",
        tex_autofigures_root=tex_root / "autofigures",
        tex_autostats_path=tex_root / "autostats.tex",
        tex_autotables_path=tex_root / "autotables.tex",
        entrypoint=publication_root / PUBLICATION_ENTRYPOINT,
        config_path=publication_root / PUBLICATION_CONFIG,
    )


def _build_source_adapters(workspace_root: Path, sources: dict[str, str]) -> dict[str, pubify_data.PublicationAdapter]:
    adapters: dict[str, pubify_data.PublicationAdapter] = {}
    for source_id, source_root in sources.items():
        source_path = Path(source_root).expanduser()
        if not source_path.is_absolute():
            source_path = (workspace_root / source_path).resolve()
        adapters[source_id] = pubify_data.PublicationAdapter(
            publication_id=source_id,
            publication_root=source_path,
            entrypoint=source_path / PUBLICATION_ENTRYPOINT,
            data_root=source_path / "data",
            external_data_roots={},
            workspace=pubify_data.WorkspaceAdapter(workspace_root),
        )
    return adapters
