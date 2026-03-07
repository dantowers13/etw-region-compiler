"""
ESF data types and node representation.

Reference: http://t-a-w.blogspot.com/2012/03/esf-empire-total-war-object.html
Format variant: ABCE (ETW / Napoleon TW)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

# ABCE magic number
ESF_MAGIC_ABCE = 0xABCE

# Primitive type tags
TYPE_INT8       = 0x00
TYPE_INT16      = 0x01
TYPE_INT32      = 0x04
TYPE_INT64      = 0x05
TYPE_UINT8      = 0x06
TYPE_FLOAT32    = 0x07
TYPE_UINT32     = 0x08
TYPE_FLOAT64    = 0x09
TYPE_BOOL8      = 0x01
TYPE_CHAR8      = 0x0A
TYPE_CHAR16     = 0x0B
TYPE_ASCII_STR  = 0x0F
TYPE_UNICODE    = 0x10
TYPE_RECORD     = 0x80   # single named record (node)
TYPE_RECORD_ARY = 0x81   # array of named records

# Pathfinding-specific semantic tags (from taw's xml2esf source)
# Verified encoding: (row<<16)|col paired with cell_id as uint32 pairs
SEMANTIC_CELL_ID_COORDS = "cell_id_coords"
SEMANTIC_BOUNDARIES     = "boundaries"


@dataclass
class ESFNode:
    """A node in the ESF tree. Leaf nodes have data; branch nodes have children."""
    tag: str
    children: list[ESFNode] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    data: Any = None        # set for leaf nodes (primitive values or packed arrays)
    raw_bytes: bytes = b""  # preserved for unknown/unparsed node types


@dataclass
class CellIdEntry:
    """Decoded entry from a cell_id_coords block."""
    row: int
    col: int
    cell_id: int

    @classmethod
    def from_packed(cls, packed_pos: int, cell_id: int) -> "CellIdEntry":
        """Decode from the two-uint32 packed representation."""
        row = (packed_pos >> 16) & 0xFFFF
        col = packed_pos & 0xFFFF
        return cls(row=row, col=col, cell_id=cell_id)

    def to_packed(self) -> tuple[int, int]:
        """Encode to the two-uint32 packed representation."""
        return ((self.row << 16) | self.col), self.cell_id


@dataclass
class BoundaryEntry:
    """Decoded boundary record.

    NOTE: Exact field semantics TBD — to be filled in from Phase 1 / ETWPC-8.
    Encoding confirmed from taw's xml2esf:
        u32_1 = (passable_part << 24) | (unknown2 << 4) | path_type
        u32_2 = (path_id << 22) | vertex_index
    """
    path_id: int = 0
    vertex_index: int = 0
    passable_part: int = 0
    unknown2: int = 0
    path_type: int = 0  # ETWPC-8: determine semantics

    @classmethod
    def from_packed(cls, u32_1: int, u32_2: int) -> "BoundaryEntry":
        passable_part = (u32_1 >> 24) & 0xFF
        unknown2      = (u32_1 >>  4) & 0xFFFFF
        path_type     = u32_1 & 0xF
        path_id       = (u32_2 >> 22) & 0x3FF
        vertex_index  = u32_2 & 0x3FFFFF
        return cls(path_id, vertex_index, passable_part, unknown2, path_type)

    def to_packed(self) -> tuple[int, int]:
        u32_1 = ((self.passable_part & 0xFF) << 24) | \
                ((self.unknown2 & 0xFFFFF) << 4) | \
                (self.path_type & 0xF)
        u32_2 = ((self.path_id & 0x3FF) << 22) | (self.vertex_index & 0x3FFFFF)
        return u32_1, u32_2
