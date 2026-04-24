from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
import ast

from pubify_mpl import DEFAULT_TEMPLATE as PUBIFY_DEFAULT_TEMPLATE
from pubify_pubs.stubs import render_init_figures_module


SYNC_STATE_FILENAME = ".pubs-sync.yaml"
WORKSPACE_CONFIG_FILENAME = "pubify.yaml"
WORKSPACE_CONFIG_SECTION = "pubify-pubs"
DEFAULT_PUBIFY_DEFAULTS = {
    "layout": "one",
}
DEFAULT_PREVIEW_BACKEND = "preview"
ALLOWED_PREVIEW_BACKENDS = {"preview", "vscode"}


@dataclass(frozen=True)
class PubifyMplConfig:
    """Publication-scoped pubify-mpl template and default export options."""

    template: dict[str, object]
    defaults: dict[str, object] = field(default_factory=dict)

    @property
    def default_layout(self) -> str:
        layout = self.defaults.get("layout")
        if not isinstance(layout, str) or not layout:
            raise ValueError("pub.yaml pubify-mpl-defaults.layout must be a non-empty string")
        return layout


@dataclass(frozen=True)
class PublicationConfig:
    """Publication-local workflow settings loaded from ``pub.yaml``."""

    publication_id: str
    title: str | None = None
    main_tex: str = "main.tex"
    mirror_root: str | None = None
    external_data_roots: dict[str, str] = field(default_factory=dict)
    sync_excludes: tuple[str, ...] = field(default_factory=tuple)
    pubify_mpl: PubifyMplConfig = field(default_factory=lambda: PubifyMplConfig(template={}, defaults={}))

    @property
    def main_tex_path(self) -> Path:
        return Path(self.main_tex)

    @property
    def mirror_root_path(self) -> Path | None:
        if self.mirror_root is None or not self.mirror_root.strip():
            return None
        return Path(self.mirror_root).expanduser()


@dataclass(frozen=True)
class PreviewConfig:
    """Workspace-level preview backend settings loaded from ``pubify.yaml``."""

    publication: str = DEFAULT_PREVIEW_BACKEND
    figure: str = DEFAULT_PREVIEW_BACKEND


@dataclass(frozen=True)
class WorkspaceConfig:
    """Workspace-level publication roots loaded from ``pubify.yaml``."""

    workspace_root: Path
    publications_root: Path
    preview: PreviewConfig


def load_publication_config(path: Path, folder_publication_id: str) -> PublicationConfig:
    """Load and validate one ``pub.yaml`` file for a publication folder."""

    raw = _parse_simple_yaml(path.read_text(encoding="utf-8"))
    declared_id = raw.get("publication_id", folder_publication_id)
    if declared_id != folder_publication_id:
        raise ValueError(
            f"{path}: publication_id '{declared_id}' does not match folder name '{folder_publication_id}'"
        )

    sync_excludes = raw.get("sync_excludes", [])
    if not isinstance(sync_excludes, list) or not all(
        isinstance(item, str) for item in sync_excludes
    ):
        raise ValueError(f"{path}: sync_excludes must be a list of strings")

    main_tex = raw.get("main_tex", "main.tex")
    if not isinstance(main_tex, str):
        raise ValueError(f"{path}: main_tex must be a string")

    mirror_root = raw.get("mirror_root")
    if mirror_root is not None and not isinstance(mirror_root, str):
        raise ValueError(f"{path}: mirror_root must be a string when set")

    external_data_roots = raw.get("external_data_roots", {})
    if not isinstance(external_data_roots, dict):
        raise ValueError(f"{path}: external_data_roots must be a mapping when set")
    workspace_root: Path | None = None
    normalized_external_roots: dict[str, str] = {}
    for root_name, root_path in external_data_roots.items():
        if not isinstance(root_name, str) or not root_name:
            raise ValueError(f"{path}: external_data_roots keys must be non-empty strings")
        if not isinstance(root_path, str) or not root_path:
            raise ValueError(
                f"{path}: external_data_roots.{root_name} must be a non-empty string"
            )
        resolved_root = Path(root_path).expanduser()
        if not resolved_root.is_absolute():
            if workspace_root is None:
                workspace_root = find_workspace_root(path.parent)
            resolved_root = (workspace_root / resolved_root).resolve()
        normalized_external_roots[root_name] = str(resolved_root)

    title = raw.get("title")
    if title is not None and not isinstance(title, str):
        raise ValueError(f"{path}: title must be a string when set")

    template = raw.get("pubify-mpl-template")
    if not isinstance(template, dict) or not template:
        raise ValueError(f"{path}: pubify-mpl-template must be a non-empty mapping")
    defaults = raw.get("pubify-mpl-defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError(f"{path}: pubify-mpl-defaults must be a mapping when set")
    if not isinstance(defaults.get("layout"), str) or not defaults.get("layout"):
        raise ValueError(f"{path}: pubify-mpl-defaults.layout must be a non-empty string")

    return PublicationConfig(
        publication_id=folder_publication_id,
        title=title,
        main_tex=main_tex,
        mirror_root=mirror_root,
        external_data_roots=normalized_external_roots,
        sync_excludes=tuple(sync_excludes),
        pubify_mpl=PubifyMplConfig(
            template=dict(template),
            defaults=dict(defaults),
        ),
    )


def find_workspace_root(start: Path | None = None) -> Path:
    """Walk upward from ``start`` until a publication workspace root is found."""

    current = (start or Path.cwd()).resolve()
    for path in (current, *current.parents):
        if (path / WORKSPACE_CONFIG_FILENAME).exists():
            return path
    raise FileNotFoundError("Could not locate workspace root from current working directory")


def load_workspace_config(workspace_root: Path) -> WorkspaceConfig:
    """Load workspace-level publication settings from ``pubify.yaml``."""

    root = workspace_root.resolve()
    config_path = root / WORKSPACE_CONFIG_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(f"Missing workspace config: {config_path}")

    raw = _parse_simple_yaml(config_path.read_text(encoding="utf-8"))
    section = _workspace_config_section(raw, config_path)
    publications_root = _require_workspace_relative_root(
        section,
        config_path,
        "publications_root",
    )
    preview = _load_preview_config(section, config_path)
    return WorkspaceConfig(
        workspace_root=root,
        publications_root=publications_root,
        preview=preview,
    )


def write_default_workspace_config(path: Path) -> None:
    """Write the default ``pubify.yaml`` scaffold for ``pubs init``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_default_workspace_config(), encoding="utf-8")


def write_skeleton_publication_config(path: Path, publication_id: str) -> None:
    """Write the default ``pub.yaml`` scaffold for ``pubs init``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_skeleton_publication_config(publication_id), encoding="utf-8")


def write_skeleton_figures_module(path: Path) -> None:
    """Write the default ``figures.py`` scaffold for a new publication."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_init_figures_module(), encoding="utf-8")


def write_skeleton_main_tex(path: Path) -> None:
    """Write the default ``main.tex`` scaffold for a new publication."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_load_init_asset_text("main.tex"), encoding="utf-8")


def write_publications_agents_file(path: Path) -> None:
    """Write the shared publications-root ``AGENTS.md`` scaffold for ``pubs init``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_load_init_asset_text("AGENTS.example.md"), encoding="utf-8")


def dump_sync_state(file_hashes: dict[str, str]) -> str:
    """Serialize the sync manifest stored in local and mirror TeX trees."""

    lines = ["files:"]
    for rel_path, digest in sorted(file_hashes.items()):
        lines.append(f"  {rel_path}: {digest}")
    return "\n".join(lines) + "\n"


def _render_skeleton_publication_config(publication_id: str) -> str:
    lines = [
        'mirror_root: ""',
        "main_tex: main.tex",
        "external_data_roots:",
        "  project: output",
        "pubify-mpl-template:",
    ]
    for key, value in PUBIFY_DEFAULT_TEMPLATE.items():
        lines.append(f"  {key}: {value}")
    lines.append("pubify-mpl-defaults:")
    for key, value in DEFAULT_PUBIFY_DEFAULTS.items():
        lines.append(f"  {key}: {value}")
    return "\n".join(lines) + "\n"


def _load_init_asset_text(filename: str) -> str:
    asset = resources.files("pubify_pubs.assets.init").joinpath(filename)
    return asset.read_text(encoding="utf-8")


def resolve_publication_data_root(workspace: WorkspaceConfig, publication_id: str) -> Path:
    """Resolve the publication-local pinned-data root."""

    return workspace.publications_root / publication_id / "data"


def load_sync_state(path: Path) -> dict[str, str]:
    """Load the sync manifest recorded in the hidden sync-state file."""

    if not path.exists():
        return {}
    raw = _parse_simple_yaml(path.read_text(encoding="utf-8"))
    files = raw.get("files", {})
    if not isinstance(files, dict):
        raise ValueError(f"{path}: files must be a mapping")
    manifest: dict[str, str] = {}
    for rel_path, digest in files.items():
        if not isinstance(rel_path, str) or not rel_path:
            raise ValueError(f"{path}: sync manifest paths must be non-empty strings")
        if not isinstance(digest, str) or not digest:
            raise ValueError(f"{path}: sync manifest hash for {rel_path!r} must be a non-empty string")
        manifest[rel_path] = digest
    return manifest


def _require_workspace_relative_root(
    raw: dict[str, object],
    config_path: Path,
    key: str,
) -> Path:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{config_path}: {key} must be a non-empty string")
    resolved = Path(value).expanduser()
    if not resolved.is_absolute():
        resolved = (config_path.parent / resolved).resolve()
    return resolved


def _load_preview_config(raw: dict[str, object], config_path: Path) -> PreviewConfig:
    preview_raw = raw.get("preview", {})
    if not isinstance(preview_raw, dict):
        raise ValueError(f"{config_path}: preview must be a mapping when set")

    publication = _validate_preview_backend(
        preview_raw.get("publication", DEFAULT_PREVIEW_BACKEND),
        config_path,
        "preview.publication",
    )
    figure = _validate_preview_backend(
        preview_raw.get("figure", DEFAULT_PREVIEW_BACKEND),
        config_path,
        "preview.figure",
    )
    return PreviewConfig(publication=publication, figure=figure)


def _validate_preview_backend(value: object, config_path: Path, key: str) -> str:
    if not isinstance(value, str) or value not in ALLOWED_PREVIEW_BACKENDS:
        allowed = ", ".join(sorted(ALLOWED_PREVIEW_BACKENDS))
        raise ValueError(f"{config_path}: {key} must be one of: {allowed}")
    return value


def _render_default_workspace_config() -> str:
    return "\n".join(
        [
            f"{WORKSPACE_CONFIG_SECTION}:",
            "  publications_root: papers",
            "  preview:",
            "    publication: preview",
            "    figure: preview",
            "",
        ]
    )


def _workspace_config_section(
    raw: dict[str, object],
    config_path: Path,
) -> dict[str, object]:
    if WORKSPACE_CONFIG_SECTION not in raw:
        raise ValueError(f"{config_path}: missing required {WORKSPACE_CONFIG_SECTION} section")
    section = raw[WORKSPACE_CONFIG_SECTION]
    if not isinstance(section, dict):
        raise ValueError(f"{config_path}: {WORKSPACE_CONFIG_SECTION} must be a mapping")
    return section


def _parse_simple_yaml(text: str) -> dict[str, object]:
    """Parse a small YAML subset used by publication configs and sync state."""

    lines = _clean_yaml_lines(text)
    parsed, next_index = _parse_mapping(lines, 0, 0)
    if next_index != len(lines):
        raise ValueError(f"Unexpected trailing YAML content near line {next_index + 1}")
    return parsed


def _clean_yaml_lines(text: str) -> list[tuple[int, str]]:
    cleaned: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        cleaned.append((indent, line.strip()))
    return cleaned


def _parse_mapping(
    lines: list[tuple[int, str]],
    start: int,
    indent: int,
) -> tuple[dict[str, object], int]:
    data: dict[str, object] = {}
    i = start

    while i < len(lines):
        current_indent, content = lines[i]
        if current_indent < indent:
            break
        if current_indent != indent:
            raise ValueError(f"Invalid indentation near line {i + 1}")
        if content.startswith("- "):
            raise ValueError(f"Unexpected list item near line {i + 1}")
        if ":" not in content:
            raise ValueError(f"Invalid YAML line near line {i + 1}: {content!r}")

        key, value = content.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Invalid YAML key near line {i + 1}")

        if value:
            data[key] = _parse_scalar(value)
            i += 1
            continue

        i += 1
        if i >= len(lines) or lines[i][0] <= current_indent:
            data[key] = {}
            continue
        child_indent, child_content = lines[i]
        if child_content.startswith("- "):
            value_list, i = _parse_list(lines, i, child_indent)
            data[key] = value_list
            continue
        child_map, i = _parse_mapping(lines, i, child_indent)
        data[key] = child_map

    return data, i


def _parse_list(
    lines: list[tuple[int, str]],
    start: int,
    indent: int,
) -> tuple[list[object], int]:
    values: list[object] = []
    i = start

    while i < len(lines):
        current_indent, content = lines[i]
        if current_indent < indent:
            break
        if current_indent != indent or not content.startswith("- "):
            raise ValueError(f"Invalid list indentation near line {i + 1}")
        item = content[2:].strip()
        if not item:
            raise ValueError(f"List items must be scalar values near line {i + 1}")
        values.append(_parse_scalar(item))
        i += 1

    return values, i


def _parse_scalar(value: str) -> object:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    if value.startswith(('"', "'")):
        return ast.literal_eval(value)
    if value.startswith("[") and value.endswith("]"):
        parsed = ast.literal_eval(value)
        if not isinstance(parsed, list):
            raise ValueError(f"Expected list literal, got {value!r}")
        return parsed
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value
