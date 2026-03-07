"""
High-level interface for regions.esf.

Exposes region outlines as shapely.Polygon objects.
Implement per ETWPC-14.
"""
from __future__ import annotations
from pathlib import Path


class RegionsFile:
    """Wraps regions.esf — load region outlines as polygons."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._load()

    def _load(self) -> None:
        raise NotImplementedError("ETWPC-14: implement after ESFReader (ETWPC-11)")

    def get_region_polygon(self, region_name: str):  # type: ignore[return]
        """Return the shapely.Polygon outline for the named region."""
        raise NotImplementedError("ETWPC-14: implement get_region_polygon")

    def list_regions(self) -> list[str]:
        raise NotImplementedError("ETWPC-14: implement list_regions")
