"""
Boundary record generator.

Generates boundary records for a new region's perimeter cells.
Field semantics TBD — resolve in ETWPC-8 (may be cosmetic only).
Implement per ETWPC-18.
"""
from __future__ import annotations
import numpy as np

from etwpc.io.esf_types import BoundaryEntry


def generate_boundaries(
    cells: np.ndarray,
    new_cell_id: int,
) -> list[BoundaryEntry]:
    """
    Generate boundary records for all perimeter cells of new_cell_id in `cells`.
    ETWPC-8: determine if boundaries are required for army movement or cosmetic.
    """
    raise NotImplementedError("ETWPC-18: implement after ETWPC-8 (boundary semantics)")
