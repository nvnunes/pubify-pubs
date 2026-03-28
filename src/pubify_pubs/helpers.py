from __future__ import annotations

from collections.abc import Mapping

import numpy as np


def require_mapping(raw_data: object, label: str) -> Mapping[str, object]:
    """Return one mapping input with a consistent type error."""

    if not isinstance(raw_data, Mapping):
        raise TypeError(f"{label} must be a mapping")
    return raw_data


def as_array(data: Mapping[str, object], key: str) -> np.ndarray:
    """Return one required mapping value as a float array."""

    if key not in data:
        raise KeyError(f"Missing required key '{key}'")
    return np.asarray(data[key], dtype=float)
