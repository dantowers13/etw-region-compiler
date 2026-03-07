"""
Connected-component validator.

Verifies that the rasterized region is contiguous (single connected component)
and that the parent region remains contiguous after the split.
Implement per ETWPC-17.
"""
from __future__ import annotations
import numpy as np


def validate_region_connectivity(cells: np.ndarray, cell_id: int) -> bool:
    """
    Return True iff all cells with `cell_id` form a single connected component.
    Uses 4-connectivity (no diagonals — matches ETW army movement).
    """
    raise NotImplementedError("ETWPC-17: implement connected-component check")


def validate_split(
    cells_before: np.ndarray,
    cells_after: np.ndarray,
    parent_id: int,
    new_id: int,
) -> list[str]:
    """
    Validate a region split. Return list of error strings (empty = pass).
    Checks: new region connected, parent still connected, no orphan cells.
    """
    raise NotImplementedError("ETWPC-17: implement split validation")
