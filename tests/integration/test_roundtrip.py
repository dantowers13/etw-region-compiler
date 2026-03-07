"""
ETWPC-26 — Round-trip fidelity tests.
These require extracted game files and are skipped if not present.

TO RUN:
    Populate data/gc/ with extracted pathfinding.esf and regions.esf
    pytest tests/integration/test_roundtrip.py -v
"""
import pytest
from pathlib import Path

DATA_DIR    = Path(__file__).parent.parent.parent / "data" / "gc"
PATHFINDING = DATA_DIR / "pathfinding.esf"
REGIONS     = DATA_DIR / "regions.esf"


def requires_game_files(*paths):
    """Skip test if any of the required game files are absent."""
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        pytest.skip(f"Game files not present: {', '.join(missing)}")


def test_pathfinding_roundtrip(tmp_path):
    """Read pathfinding.esf, write it back, verify byte-for-byte identity."""
    requires_game_files(PATHFINDING)
    raise NotImplementedError("ETWPC-26: implement after ETWPC-12 (ESFWriter)")


def test_regions_roundtrip(tmp_path):
    """Read regions.esf, write it back, verify byte-for-byte identity."""
    requires_game_files(REGIONS)
    raise NotImplementedError("ETWPC-26: implement after ETWPC-12 (ESFWriter)")
