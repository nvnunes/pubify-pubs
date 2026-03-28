from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile

from pubify_pubs.discovery import PublicationDefinition, load_publication_definition


PIN_SIZE_WARNING_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class PinItem:
    """One source-to-destination copy step required to pin a loader."""

    relative_path: str
    source_path: Path
    target_path: Path
    size_bytes: int
    is_dir: bool


@dataclass(frozen=True)
class PinResult:
    """Summary of one successful loader pin operation."""

    loader_id: str
    copied_paths: tuple[str, ...]
    decorator_summary: str


def pin_loader(publication: PublicationDefinition, loader_id: str) -> PinResult:
    """Copy one external-data loader's declared inputs into pinned publication data."""

    loader = publication.loaders.get(loader_id)
    if loader is None:
        raise KeyError(f"Unknown loader '{loader_id}'")
    if loader.kind != "external_data":
        raise ValueError(f"Loader '{loader_id}' is not declared with @external_data")
    if loader.root_name is None:
        raise ValueError(f"Loader '{loader_id}' does not declare an external data root")
    root_value = publication.config.external_data_roots.get(loader.root_name)
    if root_value is None:
        raise ValueError(
            f"Loader '{loader_id}' references undefined external data root '{loader.root_name}'"
        )

    source_root = Path(root_value)
    plan = _build_pin_plan(
        publication,
        loader_id,
        source_root,
        tuple(loader.relative_paths.values()),
    )
    decorator_summary = _render_pinned_decorator(loader.style, loader.relative_paths, loader.nocache)
    original_text = publication.paths.entrypoint.read_text(encoding="utf-8")
    rewritten_text = _rewrite_loader_to_pinned(
        original_text,
        loader_id=loader_id,
        decorator_summary=decorator_summary,
    )

    publication.paths.data_root.parent.mkdir(parents=True, exist_ok=True)
    backup_root = Path(
        tempfile.mkdtemp(prefix="pubs-pin-", dir=publication.paths.data_root.parent)
    )
    snapshots = _snapshot_targets(plan, backup_root)
    try:
        _apply_copy_plan(plan)
        publication.paths.entrypoint.write_text(rewritten_text, encoding="utf-8")
        _validate_pinned_loader(publication, loader_id, tuple(loader.relative_paths.values()))
    except Exception:
        publication.paths.entrypoint.write_text(original_text, encoding="utf-8")
        _restore_targets(plan, snapshots)
        raise
    finally:
        shutil.rmtree(backup_root, ignore_errors=True)

    return PinResult(
        loader_id=loader_id,
        copied_paths=tuple(
            str(item.target_path.relative_to(publication.paths.workspace_root).as_posix())
            for item in plan
        ),
        decorator_summary=decorator_summary,
    )


def _build_pin_plan(
    publication: PublicationDefinition,
    loader_id: str,
    source_root: Path,
    relative_paths: tuple[str, ...],
) -> list[PinItem]:
    _ensure_non_overlapping_paths(relative_paths, loader_id)
    plan: list[PinItem] = []
    total_bytes = 0
    for relative_path in relative_paths:
        source_path = source_root / relative_path
        if not source_path.exists():
            raise FileNotFoundError(
                f"Missing external data path for loader '{loader_id}': {source_path}"
            )
        target_path = publication.paths.data_root / relative_path
        item = PinItem(
            relative_path=relative_path,
            source_path=source_path,
            target_path=target_path,
            size_bytes=_measure_path(source_path),
            is_dir=source_path.is_dir(),
        )
        _validate_target_path(item, loader_id)
        total_bytes += item.size_bytes
        plan.append(item)

    if total_bytes > PIN_SIZE_WARNING_BYTES:
        affected = "\n".join(f"- {item.relative_path}" for item in plan)
        raise ValueError(
            f"Pinning loader '{loader_id}' exceeds the safe copy limit "
            f"({_format_bytes(total_bytes)} > {_format_bytes(PIN_SIZE_WARNING_BYTES)}).\n"
            f"Affected paths:\n{affected}"
        )
    return plan


def _measure_path(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def _ensure_non_overlapping_paths(relative_paths: tuple[str, ...], loader_id: str) -> None:
    parts = [(path, Path(path).parts) for path in relative_paths]
    for index, (left, left_parts) in enumerate(parts):
        for right, right_parts in parts[index + 1 :]:
            shorter = min(len(left_parts), len(right_parts))
            if left_parts[:shorter] == right_parts[:shorter]:
                raise ValueError(
                    f"Loader '{loader_id}' has overlapping pin paths that cannot be pinned "
                    f"mechanically: {left} and {right}"
                )


def _validate_target_path(item: PinItem, loader_id: str) -> None:
    target = item.target_path
    source = item.source_path
    if not target.exists():
        return
    if source.is_file() != target.is_file():
        raise ValueError(
            f"Loader '{loader_id}' would replace an existing pinned path ambiguously: {target}"
        )
    if source.is_file():
        if source.read_bytes() != target.read_bytes():
            raise ValueError(
                f"Loader '{loader_id}' would overwrite an existing pinned file with different "
                f"contents: {target}"
            )
        return
    if source.is_dir():
        source_entries = _collect_tree_entries(source, include_contents=True)
        target_entries = _collect_tree_entries(target, include_contents=True)
        extra_entries = sorted(set(target_entries) - set(source_entries))
        if extra_entries or source_entries != target_entries:
            raise ValueError(
                f"Loader '{loader_id}' would overwrite an existing pinned directory with "
                f"different contents: {target}"
            )


def _collect_tree_entries(path: Path, *, include_contents: bool) -> dict[str, tuple[bool, bytes | None]]:
    entries: dict[str, tuple[bool, bytes | None]] = {}
    for child in sorted(path.rglob("*")):
        rel = child.relative_to(path).as_posix()
        if child.is_dir():
            entries[rel] = (True, None)
        elif include_contents:
            entries[rel] = (False, child.read_bytes())
        else:
            entries[rel] = (False, None)
    return entries


def _render_pinned_decorator(
    style: str,
    relative_paths: dict[str, str],
    nocache: bool,
) -> str:
    if style == "single":
        args = [repr(next(iter(relative_paths.values())))]
    elif style == "named":
        args = [f"{name}={value!r}" for name, value in relative_paths.items()]
    else:
        raise ValueError(f"Unsupported loader style '{style}' for pinning")
    if nocache:
        args.append("nocache=True")
    return f"@data({', '.join(args)})"


def _rewrite_loader_to_pinned(
    text: str,
    *,
    loader_id: str,
    decorator_summary: str,
) -> str:
    tree = ast.parse(text)
    function_name = f"load_{loader_id}"
    target_func: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            target_func = node
            break
    if target_func is None:
        raise ValueError(f"Could not locate loader function '{function_name}' in figures.py")
    if len(target_func.decorator_list) != 1:
        raise ValueError(
            f"Loader '{loader_id}' cannot be pinned mechanically because it does not have "
            "exactly one decorator"
        )
    decorator = target_func.decorator_list[0]
    if not (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "external_data"
    ):
        raise ValueError(
            f"Loader '{loader_id}' cannot be pinned mechanically because its decorator is not "
            "@external_data(...)"
        )

    lines = text.splitlines(keepends=True)
    start = decorator.lineno - 1
    end = decorator.end_lineno
    lines[start:end] = [decorator_summary + "\n"]
    rewritten = "".join(lines)
    return _ensure_data_import(rewritten)


def _ensure_data_import(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        prefix = "from pubify_pubs.decorators import "
        if not line.startswith(prefix):
            continue
        if "(" in line or ")" in line:
            raise ValueError(
                "Cannot pin loader mechanically because the decorators import uses multiline syntax"
            )
        imported = [item.strip() for item in line[len(prefix) :].split(",")]
        if "data" in imported:
            return text
        imported = ["data", *imported]
        lines[index] = prefix + ", ".join(imported)
        return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    raise ValueError(
        "Cannot pin loader mechanically because figures.py does not contain "
        "'from pubify_pubs.decorators import ...'"
    )


def _snapshot_targets(plan: list[PinItem], backup_root: Path) -> dict[str, Path | None]:
    snapshots: dict[str, Path | None] = {}
    for item in plan:
        if not item.target_path.exists():
            snapshots[item.relative_path] = None
            continue
        backup_path = backup_root / item.relative_path
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if item.target_path.is_dir():
            shutil.copytree(item.target_path, backup_path)
        else:
            shutil.copy2(item.target_path, backup_path)
        snapshots[item.relative_path] = backup_path
    return snapshots


def _apply_copy_plan(plan: list[PinItem]) -> None:
    for item in plan:
        item.target_path.parent.mkdir(parents=True, exist_ok=True)
        if item.is_dir:
            if item.target_path.exists():
                shutil.rmtree(item.target_path)
            shutil.copytree(item.source_path, item.target_path)
        else:
            shutil.copy2(item.source_path, item.target_path)


def _restore_targets(plan: list[PinItem], snapshots: dict[str, Path | None]) -> None:
    for item in reversed(plan):
        backup_path = snapshots[item.relative_path]
        if item.target_path.exists():
            if item.target_path.is_dir():
                shutil.rmtree(item.target_path)
            else:
                item.target_path.unlink()
        if backup_path is None:
            continue
        item.target_path.parent.mkdir(parents=True, exist_ok=True)
        if backup_path.is_dir():
            shutil.copytree(backup_path, item.target_path)
        else:
            shutil.copy2(backup_path, item.target_path)


def _validate_pinned_loader(
    publication: PublicationDefinition,
    loader_id: str,
    relative_paths: tuple[str, ...],
) -> None:
    reloaded = load_publication_definition(
        publication.paths.workspace_root,
        publication.publication_id,
    )
    loader = reloaded.loaders.get(loader_id)
    if loader is None:
        raise ValueError(f"Loader '{loader_id}' is not discoverable after pinning")
    if loader.kind != "data":
        raise ValueError(f"Loader '{loader_id}' was not rewritten to @data(...)")
    for relative_path in relative_paths:
        pinned_path = reloaded.paths.data_root / relative_path
        if not pinned_path.exists():
            raise ValueError(f"Pinned data path is missing after pinning: {pinned_path}")


def _format_bytes(num_bytes: int) -> str:
    units = ["B", "KiB", "MiB", "GiB"]
    value = float(num_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num_bytes} B"
