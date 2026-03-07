"""
TGA colour mask → shapely.Polygon.

Reads map_regions.tga, finds pixels matching a given RGB colour,
and traces the contour into a polygon.
Implement per ETWPC-20.
"""
from __future__ import annotations
from pathlib import Path


def tga_colour_to_polygon(tga_path: Path, rgb: tuple[int, int, int]):  # type: ignore[return]
    """
    Extract the polygon outline for the region identified by `rgb` in map_regions.tga.
    Returns a shapely.Polygon in TGA pixel coordinates.
    Convert to world coordinates with coords.pixel_to_world afterwards.
    """
    raise NotImplementedError("ETWPC-20: implement TGA contour tracing")
