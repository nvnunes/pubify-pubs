from __future__ import annotations

from pathlib import Path

import numpy as np

from pubify_pubs.config import load_workspace_config, resolve_publication_data_root
from pubify_pubs.discovery import find_workspace_root


def publication_data_path(
    publication_id: str,
    relative_path: str,
    *,
    workspace_root: Path | None = None,
) -> Path:
    """Resolve one pinned publication-data path under the configured workspace ``data_root``."""

    resolved_workspace_root = (workspace_root or find_workspace_root()).resolve()
    workspace = load_workspace_config(resolved_workspace_root)
    destination = (
        resolve_publication_data_root(workspace, publication_id)
        / _validate_publication_relative_path(relative_path)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def load_publication_data_npz(path: Path) -> dict[str, np.ndarray]:
    """Load a pinned ``.npz`` file into detached NumPy arrays."""

    resolved_path = Path(path)
    _validate_npz_file_path(resolved_path)
    with np.load(resolved_path) as saved:
        return {name: np.array(saved[name], copy=True) for name in saved.files}


def save_publication_data_npz(
    publication_id: str,
    relative_path: str,
    *,
    overwrite: bool = False,
    workspace_root: Path | None = None,
    **arrays: object,
) -> Path:
    """Save arrays as a pinned ``.npz`` file under a publication's data root."""

    _validate_npz_relative_path(relative_path)
    destination = publication_data_path(
        publication_id,
        relative_path,
        workspace_root=workspace_root,
    )
    if destination.exists() and not overwrite:
        raise FileExistsError(
            f"Publication data target already exists: {destination}. Pass overwrite=True to replace it."
        )
    np.savez(destination, **arrays)
    return destination


def _validate_publication_relative_path(relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute():
        raise ValueError(f"Publication data path must be relative, not absolute: {relative_path}")
    if ".." in path.parts:
        raise ValueError(
            f"Publication data path must stay under the publication data root: {relative_path}"
        )
    return path


def _validate_npz_relative_path(relative_path: str) -> None:
    path = _validate_publication_relative_path(relative_path)
    if path.suffix != ".npz":
        raise ValueError(f"Publication data path must end with .npz: {relative_path}")


def _validate_npz_file_path(path: Path) -> None:
    if path.suffix != ".npz":
        raise ValueError(f"Publication data file must end with .npz: {path}")
    if not path.exists():
        raise FileNotFoundError(f"Publication data file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"Publication data path must be a file: {path}")
