from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def figure(func: Callable) -> Callable:
    """Mark a callable as an exported publication figure entrypoint."""

    setattr(func, "__pubs_figure__", True)
    return func


def stat(func: Callable) -> Callable:
    """Mark a callable as a computed publication stat entrypoint."""

    setattr(func, "__pubs_stat__", True)
    return func


def table(func: Callable) -> Callable:
    """Mark a callable as a generated publication table entrypoint."""

    setattr(func, "__pubs_table__", True)
    return func


def data(
    *args: str,
    nocache: bool = False,
    **paths: str,
) -> Callable[[Callable], Callable]:
    """Declare a loader that reads pinned publication-local data from ``data_root``."""

    if args and paths:
        raise ValueError("@data accepts either one positional path or named paths, not both")
    if not args and not paths:
        raise ValueError("@data requires exactly one positional path or one-or-more named paths")
    if len(args) > 1:
        raise ValueError("@data accepts at most one positional path")
    if args:
        data_style = "single"
        resolved_paths = {"path": _validate_loader_relative_path(args[0], decorator_name="@data")}
    else:
        data_style = "named"
        resolved_paths = {
            name: _validate_loader_relative_path(value, decorator_name="@data")
            for name, value in paths.items()
        }

    def decorate(func: Callable) -> Callable:
        setattr(
            func,
            "__pubs_loader__",
            {
                "kind": "data",
                "style": data_style,
                "paths": resolved_paths,
                "nocache": nocache,
            },
        )
        return func

    return decorate


def external_data(
    root_name: str,
    *args: str,
    nocache: bool = False,
    **paths: str,
) -> Callable[[Callable], Callable]:
    """Declare a loader that reads from one configured external data root."""

    if not isinstance(root_name, str) or not root_name:
        raise ValueError("@external_data requires a non-empty root name")
    if args and paths:
        raise ValueError(
            "@external_data accepts either one positional path or named paths, not both"
        )
    if not args and not paths:
        raise ValueError(
            "@external_data requires exactly one positional path or one-or-more named paths"
        )
    if len(args) > 1:
        raise ValueError("@external_data accepts at most one positional path")
    if args:
        data_style = "single"
        resolved_paths = {
            "path": _validate_loader_relative_path(args[0], decorator_name="@external_data")
        }
    else:
        data_style = "named"
        resolved_paths = {
            name: _validate_loader_relative_path(value, decorator_name="@external_data")
            for name, value in paths.items()
        }

    def decorate(func: Callable) -> Callable:
        setattr(
            func,
            "__pubs_loader__",
            {
                "kind": "external_data",
                "root_name": root_name,
                "style": data_style,
                "paths": resolved_paths,
                "nocache": nocache,
            },
        )
        return func

    return decorate


def _validate_loader_relative_path(value: str, *, decorator_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{decorator_name} paths must be non-empty relative paths")
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"{decorator_name} paths must be relative, not absolute: {value}")
    if any(part == ".." for part in path.parts):
        raise ValueError(f"{decorator_name} paths must stay under their configured root: {value}")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise ValueError(f"{decorator_name} paths must be non-empty relative paths")
    return normalized
