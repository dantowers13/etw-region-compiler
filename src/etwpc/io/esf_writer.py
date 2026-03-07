"""
ABCE ESF binary writer.

Implement per ETWPC-12, after ESFReader (ETWPC-11) is complete.
The round-trip test (ETWPC-26) gates all in-game testing.
"""
from __future__ import annotations
import struct
from pathlib import Path
from .esf_types import ESFNode, ESF_MAGIC_ABCE


class ESFWriter:
    """Write an ESFNode tree back to ABCE ESF binary format."""

    def __init__(self, root: ESFNode, tag_names: list[str], timestamp: int = 0) -> None:
        self.root = root
        self.tag_names = tag_names
        self.timestamp = timestamp

    def write(self, path: Path) -> None:
        """Serialise the ESF tree to path."""
        raise NotImplementedError("ETWPC-12: implement ABCE writer")

    def to_bytes(self) -> bytes:
        """Serialise the ESF tree to a bytes object."""
        raise NotImplementedError("ETWPC-12: implement ABCE writer")
