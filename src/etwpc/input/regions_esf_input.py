"""
regions.esf outline extractor.

Reads region outlines directly from regions.esf (world coordinates).
Implement per ETWPC-21.
"""
from __future__ import annotations
from pathlib import Path


def regions_esf_to_polygon(regions_path: Path, region_name: str):  # type: ignore[return]
    """
    Extract the polygon outline for `region_name` from regions.esf.
    Returns a shapely.Polygon in world coordinates.
    """
    raise NotImplementedError("ETWPC-21: implement after ESFReader (ETWPC-11)")
