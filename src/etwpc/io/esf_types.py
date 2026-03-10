"""
ESF data types and node representation.

Reference: http://t-a-w.blogspot.com/2012/03/esf-empire-total-war-object.html
Format variant: ABCE (ETW / Napoleon TW)

Type codes verified against taw's esf_parser.rb dispatch table.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

# ABCE magic number
ESF_MAGIC_ABCE = 0xABCE

# ── Primitive type tags (verified against taw's esf_parser.rb) ────────────────
T_BOOL      = 0x01  # bool (1 byte: 0 or 1)
T_I1        = 0x02  # int8
T_I2        = 0x03  # int16
T_I4        = 0x04  # int32
T_I8        = 0x05  # int64
T_U1        = 0x06  # uint8
T_U2        = 0x07  # uint16
T_U4        = 0x08  # uint32
T_U8        = 0x09  # uint64
T_F4        = 0x0A  # float32
T_F8        = 0x0B  # float64
T_V2        = 0x0C  # vector2 — two float32
T_V3        = 0x0D  # vector3 — three float32
T_UNICODE   = 0x0E  # UTF-16LE string: uint16 char-count + chars
T_ASCII     = 0x0F  # ASCII string:    uint16 byte-count + bytes
T_ANGLE     = 0x10  # uint16, unit = 360/65536 degrees

# Compact encodings (no-payload or reduced-width)
T_BOOL_T    = 0x12  # bool = True  (no payload)
T_BOOL_F    = 0x13  # bool = False (no payload)
T_U4_0      = 0x14  # uint32 = 0   (no payload)
T_U4_1      = 0x15  # uint32 = 1   (no payload)
T_U4_B      = 0x16  # uint32, 1-byte payload
T_U4_W      = 0x17  # uint32, 2-byte payload
T_U4_3      = 0x18  # uint32, 3-byte payload
T_I4_0      = 0x19  # int32  = 0   (no payload)
T_I4_B      = 0x1A  # int32, 1-byte payload (sign-extended)
T_I4_W      = 0x1B  # int32, 2-byte payload (sign-extended)
T_I4_3      = 0x1C  # int32, 3-byte payload (sign-extended)

# ── Array / blob type tags ────────────────────────────────────────────────────
T_BOOL_ARY  = 0x41  # bool array
T_I1_ARY    = 0x42  # int8  array
T_I2_ARY    = 0x43  # int16 array  (signed, 's*' unpack)
T_I4_ARY    = 0x44  # int32 array
T_I8_ARY    = 0x45  # int64 array
T_U1_ARY    = 0x46  # uint8 array / raw byte blob ("bin6" in taw)
T_U2_ARY    = 0x47  # uint16 array
T_U4_ARY    = 0x48  # uint32 array
T_U8_ARY    = 0x49  # uint64 array
T_F4_ARY    = 0x4A  # float32 array (raw bytes)
T_F8_ARY    = 0x4B  # float64 array (raw bytes)
T_V2_ARY    = 0x4C  # vector2 array (raw bytes)
T_V3_ARY    = 0x4D  # vector3 array (raw bytes)
T_STR_ARY   = 0x4E  # unicode string array (raw bytes)
T_ASC_ARY   = 0x4F  # ASCII string array   (raw bytes)

# Compact array variants (alternative width encodings for uint32 arrays)
T_U4_ARY_B  = 0x55  # u4_ary stored as uint8  elements
T_U4_ARY_W  = 0x57  # u4_ary stored as uint16 elements

# ── Record type tags ──────────────────────────────────────────────────────────
T_RECORD    = 0x80  # single named record
T_RECORD_ARY = 0x81  # array of named records

# Aliases kept for backward compatibility with any code that used the old names
TYPE_INT32      = T_I4
TYPE_UINT32     = T_U4
TYPE_ASCII_STR  = T_ASCII
TYPE_UNICODE    = T_UNICODE
TYPE_RECORD     = T_RECORD
TYPE_RECORD_ARY = T_RECORD_ARY

# Tag types whose payload is a raw byte blob (length-prefixed by varint).
# All need round-trip byte preservation.
BLOB_TAGS: frozenset[int] = frozenset({
    T_U1_ARY,                          # 0x46 bin6
    0x47,                              # bin7  (taw treats u2_ary as raw in many cases)
    T_F4_ARY, T_F8_ARY,
    T_V2_ARY, T_V3_ARY,
    T_STR_ARY, T_ASC_ARY,
    *range(0x46, 0x60),                # all bin0-bin1f variants
})


@dataclass
class ESFPrimitive:
    """A typed scalar or array value in the ESF tree."""
    type_tag: int   # raw byte tag, preserved for round-trip
    value: Any      # decoded Python value; None for pure-raw blobs
    raw: bytes = b""  # preserved verbatim for blob types (T_U1_ARY etc.)


@dataclass
class ESFNode:
    """A named record node (0x80 single or 0x81 array) in the ESF tree.

    For T_RECORD (0x80):
        children: flat list[ESFPrimitive | ESFNode]

    For T_RECORD_ARY (0x81):
        children: list[list[ESFPrimitive | ESFNode]]  — one list per item
    """
    tag: str
    type_tag: int = T_RECORD            # T_RECORD or T_RECORD_ARY
    version: int = 0                    # version byte from binary
    children: list = field(default_factory=list)

    # Legacy fields — kept so existing code that reads .data / .attributes
    # doesn't break; new code should use .children.
    attributes: dict[str, Any] = field(default_factory=dict)
    data: Any = None
    raw_bytes: bytes = b""

    @property
    def is_array(self) -> bool:
        return self.type_tag == T_RECORD_ARY


# ── Pathfinding-specific packed types ────────────────────────────────────────

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

    Encoding confirmed from taw's xml2esf:
        u32_1 = (passable_part << 24) | (unknown2 << 4) | path_type
        u32_2 = (path_id << 22) | vertex_index
    """
    path_id: int = 0
    vertex_index: int = 0
    passable_part: int = 0
    unknown2: int = 0
    path_type: int = 0

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
