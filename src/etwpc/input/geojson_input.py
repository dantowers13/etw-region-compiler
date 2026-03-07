"""
GeoJSON WGS84 → ETW world coordinate polygon.

Allows drawing new region boundaries in GIS tools (QGIS, geojson.io)
and importing them as world-coordinate polygons.
Implement per ETWPC-22.
"""
from __future__ import annotations
from pathlib import Path


def geojson_to_polygon(geojson_path: Path):  # type: ignore[return]
    """
    Read a GeoJSON file (WGS84), project to ETW world coordinates,
    and return as a shapely.Polygon.
    """
    raise NotImplementedError("ETWPC-22: implement WGS84 → ETW world transform")
