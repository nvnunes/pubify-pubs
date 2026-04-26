from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import importlib.util
from pathlib import Path
import re
import sys
import sysconfig

from pubify_pubs.discovery import PublicationDefinition


NodeKey = tuple[str, str]


@dataclass(frozen=True)
class ShellMethodState:
    node_fingerprints: dict[NodeKey, str]
    imported_module_paths: tuple[Path, ...]
    imported_module_fingerprints: dict[Path, float | None]
    loader_to_figures: dict[str, tuple[str, ...]]
    loader_to_stats: dict[str, tuple[str, ...]]
    loader_to_tables: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ShellBuildPlan:
    full_refresh: bool
    changed_loader_ids: tuple[str, ...]
    figure_ids: tuple[str, ...]
    stat_ids: tuple[str, ...]
    table_ids: tuple[str, ...]
    rewrite_stats: bool = False
    rewrite_tables: bool = False


def collect_shell_method_state(publication: PublicationDefinition) -> ShellMethodState:
    source_text = publication.paths.entrypoint.read_text(encoding="utf-8")
    module_ast = ast.parse(source_text, filename=str(publication.paths.entrypoint))
    top_level_funcs = {
        node.name: node
        for node in module_ast.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    top_level_constants = {
        name: node
        for node in module_ast.body
        for name in _top_level_constant_names(node)
    }
    symbol_closures = {
        name: _symbol_closure(name, top_level_funcs, top_level_constants)
        for name in top_level_funcs
    }

    node_fingerprints: dict[NodeKey, str] = {}
    for loader_id, loader in publication.loaders.items():
        node_fingerprints[("loader", loader_id)] = _hash_function_closure(
            loader.func.__name__,
            symbol_closures,
            top_level_funcs,
            top_level_constants,
        )
    for figure_id, figure in publication.figures.items():
        node_fingerprints[("figure", figure_id)] = _hash_function_closure(
            figure.func.__name__,
            symbol_closures,
            top_level_funcs,
            top_level_constants,
        )
    for stat_id, stat in publication.stats.items():
        node_fingerprints[("stat", stat_id)] = _hash_function_closure(
            stat.func.__name__,
            symbol_closures,
            top_level_funcs,
            top_level_constants,
        )
    for table_id, table in publication.tables.items():
        node_fingerprints[("table", table_id)] = _hash_function_closure(
            table.func.__name__,
            symbol_closures,
            top_level_funcs,
            top_level_constants,
        )

    imported_module_paths = collect_local_import_module_paths(
        publication.paths.entrypoint,
        workspace_root=publication.paths.workspace_root,
        publication_root=publication.paths.publication_root,
    )

    return ShellMethodState(
        node_fingerprints=node_fingerprints,
        imported_module_paths=imported_module_paths,
        imported_module_fingerprints={path: _mtime_or_none(path) for path in imported_module_paths},
        loader_to_figures=_reverse_loader_dependencies(
            {figure_id: figure.dependency_ids for figure_id, figure in publication.figures.items()}
        ),
        loader_to_stats=_reverse_loader_dependencies(
            {stat_id: stat.dependency_ids for stat_id, stat in publication.stats.items()}
        ),
        loader_to_tables=_reverse_loader_dependencies(
            {table_id: table.dependency_ids for table_id, table in publication.tables.items()}
        ),
    )


def plan_incremental_shell_build(
    publication: PublicationDefinition,
    current_state: ShellMethodState,
    last_success_state: ShellMethodState | None,
    *,
    cached_figure_output_names: dict[str, tuple[str, ...]],
    cached_stats_complete: bool,
    cached_tables_complete: bool,
) -> ShellBuildPlan:
    if last_success_state is None:
        return ShellBuildPlan(True, (), (), (), ())

    changed_loader_ids = _changed_ids(current_state, last_success_state, "loader", publication.loaders)
    changed_figure_ids = _changed_ids(current_state, last_success_state, "figure", publication.figures)
    changed_stat_ids = _changed_ids(current_state, last_success_state, "stat", publication.stats)
    changed_table_ids = _changed_ids(current_state, last_success_state, "table", publication.tables)

    affected_figures = set(changed_figure_ids)
    for loader_id in changed_loader_ids:
        affected_figures.update(current_state.loader_to_figures.get(loader_id, ()))
    affected_stats = set(changed_stat_ids)
    for loader_id in changed_loader_ids:
        affected_stats.update(current_state.loader_to_stats.get(loader_id, ()))
    affected_tables = set(changed_table_ids)
    for loader_id in changed_loader_ids:
        affected_tables.update(current_state.loader_to_tables.get(loader_id, ()))

    affected_figures.update(_stale_figure_ids(publication, cached_figure_output_names))
    rewrite_stats = False
    rewrite_tables = False
    if publication.stats and not publication.paths.autostats_path.exists():
        if cached_stats_complete:
            rewrite_stats = True
        else:
            return ShellBuildPlan(True, (), (), (), ())
    if publication.tables and not publication.paths.autotables_path.exists():
        if cached_tables_complete:
            rewrite_tables = True
        else:
            return ShellBuildPlan(True, (), (), (), ())

    if affected_stats and not cached_stats_complete and len(affected_stats) != len(publication.stats):
        return ShellBuildPlan(True, (), (), (), ())
    if affected_tables and not cached_tables_complete and len(affected_tables) != len(publication.tables):
        return ShellBuildPlan(True, (), (), (), ())

    return ShellBuildPlan(
        full_refresh=False,
        changed_loader_ids=tuple(sorted(changed_loader_ids)),
        figure_ids=tuple(sorted(affected_figures)),
        stat_ids=tuple(sorted(affected_stats)),
        table_ids=tuple(sorted(affected_tables)),
        rewrite_stats=rewrite_stats,
        rewrite_tables=rewrite_tables,
    )


def imported_module_fingerprints_changed(previous_state: ShellMethodState, current_fingerprints: dict[Path, float | None]) -> bool:
    for path in previous_state.imported_module_paths:
        if current_fingerprints.get(path) != previous_state.imported_module_fingerprints.get(path):
            return True
    return False


def collect_local_import_module_paths(
    entrypoint: Path,
    *,
    workspace_root: Path,
    publication_root: Path,
) -> tuple[Path, ...]:
    discovered_paths: set[Path] = set()
    visited_paths: set[Path] = set()
    stack = [entrypoint.resolve()]

    while stack:
        current_path = stack.pop()
        if current_path in visited_paths or not current_path.exists():
            continue
        visited_paths.add(current_path)
        try:
            module_ast = ast.parse(current_path.read_text(encoding="utf-8"), filename=str(current_path))
        except (OSError, SyntaxError):
            continue
        for module_name in _imported_module_names(module_ast):
            try:
                spec = importlib.util.find_spec(module_name)
            except (AttributeError, ImportError, ModuleNotFoundError, ValueError):
                continue
            if spec is None or spec.origin in {None, "built-in", "frozen"}:
                continue
            origin = Path(spec.origin).resolve()
            if origin == entrypoint.resolve():
                continue
            if not _is_local_source_module(origin):
                continue
            if origin not in discovered_paths:
                discovered_paths.add(origin)
                stack.append(origin)
    return tuple(sorted(discovered_paths))


def _changed_ids(
    current_state: ShellMethodState,
    last_success_state: ShellMethodState,
    kind: str,
    nodes: dict[str, object],
) -> set[str]:
    changed: set[str] = set()
    for node_id in nodes:
        key = (kind, node_id)
        if current_state.node_fingerprints.get(key) != last_success_state.node_fingerprints.get(key):
            changed.add(node_id)
    return changed


def _stale_figure_ids(publication: PublicationDefinition, cached_output_names: dict[str, tuple[str, ...]]) -> set[str]:
    stale: set[str] = set()
    for figure_id in publication.figures:
        actual_names = _current_figure_output_names(publication, figure_id)
        expected_names = cached_output_names.get(figure_id, actual_names)
        if actual_names != expected_names:
            stale.add(figure_id)
    return stale


def _current_figure_output_names(publication: PublicationDefinition, figure_id: str) -> tuple[str, ...]:
    if not publication.paths.autofigures_root.exists():
        return ()
    names: list[str] = []
    for path in publication.paths.autofigures_root.iterdir():
        if not path.is_file():
            continue
        if figure_output_belongs_to_id(path, figure_id):
            names.append(path.name)
    return tuple(sorted(names))


def figure_output_belongs_to_id(path: Path, figure_id: str) -> bool:
    stem = path.stem
    if stem == figure_id:
        return True
    return re.fullmatch(rf"{re.escape(figure_id)}_[0-9]+", stem) is not None


def _reverse_loader_dependencies(node_dependencies: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    reverse: dict[str, list[str]] = {}
    for node_id, dependency_ids in node_dependencies.items():
        for dependency_id in dependency_ids:
            reverse.setdefault(dependency_id, []).append(node_id)
    return {key: tuple(sorted(value)) for key, value in reverse.items()}


def _symbol_closure(
    function_name: str,
    top_level_funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    top_level_constants: dict[str, ast.Assign | ast.AnnAssign],
) -> tuple[str, ...]:
    closure: set[str] = set()
    stack = [function_name]
    available_names = set(top_level_funcs) | set(top_level_constants)
    while stack:
        current_name = stack.pop()
        node = top_level_funcs.get(current_name) or top_level_constants.get(current_name)
        if node is None:
            continue
        for referenced_name in _referenced_top_level_names(node, available_names):
            if referenced_name == function_name or referenced_name in closure:
                continue
            closure.add(referenced_name)
            stack.append(referenced_name)
    return tuple(sorted(closure))


def _hash_function_closure(
    function_name: str,
    symbol_closures: dict[str, tuple[str, ...]],
    top_level_funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    top_level_constants: dict[str, ast.Assign | ast.AnnAssign],
) -> str:
    ordered_names = (function_name, *symbol_closures.get(function_name, ()))
    payload = "\n".join(
        _normalized_top_level_node(
            top_level_funcs.get(name) or top_level_constants.get(name)
        )
        for name in ordered_names
        if name in top_level_funcs or name in top_level_constants
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalized_top_level_node(node: ast.AST | None) -> str:
    if node is None:
        return ""
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _referenced_top_level_names(
    node: ast.AST,
    top_level_names: set[str],
) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and child.id in top_level_names
    }


def _top_level_constant_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Assign):
        names = [
            target.id
            for target in node.targets
            if isinstance(target, ast.Name)
        ]
        return tuple(names)
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return (node.target.id,)
    return ()


def _imported_module_names(module_ast: ast.Module) -> set[str]:
    module_names: set[str] = set()
    for child in ast.walk(module_ast):
        if isinstance(child, ast.Import):
            for alias in child.names:
                if _should_track_imported_module_name(alias.name):
                    module_names.add(alias.name)
        elif isinstance(child, ast.ImportFrom) and child.level == 0 and child.module:
            if _should_track_imported_module_name(child.module):
                module_names.add(child.module)
            for alias in child.names:
                if alias.name == "*":
                    continue
                candidate = f"{child.module}.{alias.name}"
                if _should_track_imported_module_name(candidate):
                    module_names.add(candidate)
    return module_names


def _should_track_imported_module_name(module_name: str) -> bool:
    top_level = module_name.split(".", 1)[0]
    return top_level not in {"pubify_pubs", "pubify_mpl", "pubify_tex"}


def _looks_like_site_package(path: Path) -> bool:
    parts = set(path.parts)
    return "site-packages" in parts or "dist-packages" in parts


def _is_local_source_module(path: Path) -> bool:
    if path.suffix != ".py":
        return False
    if _looks_like_site_package(path):
        return False
    return not _looks_like_stdlib(path)


def _looks_like_stdlib(path: Path) -> bool:
    stdlib = sysconfig.get_paths().get("stdlib")
    if not stdlib:
        return False
    stdlib_path = Path(stdlib).resolve()
    return path == stdlib_path or stdlib_path in path.parents


def _mtime_or_none(path: Path) -> float | None:
    if not path.exists():
        return None
    return path.stat().st_mtime


def purge_modules_by_paths(paths: Iterable[Path]) -> None:
    resolved_paths = {path.resolve() for path in paths}
    for module_name, module in list(sys.modules.items()):
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            continue
        try:
            resolved = Path(module_file).resolve()
        except OSError:
            continue
        if resolved in resolved_paths:
            sys.modules.pop(module_name, None)
