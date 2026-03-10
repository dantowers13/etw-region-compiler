"""
ABCE ESF binary reader — ETWPC-11.

Parses an ABCE-magic ESF file (ETW / Napoleon TW) into an ESFNode tree.
Every byte is accounted for so ESFWriter can reproduce the file exactly.

Type codes verified against taw's esf_parser.rb.
"""
from __future__ import annotations
import struct
from pathlib import Path

from .esf_types import (
    ESFNode, ESFPrimitive,
    ESF_MAGIC_ABCE,
    T_BOOL, T_I1, T_I2, T_I4, T_I8,
    T_U1, T_U2, T_U4, T_U8,
    T_F4, T_F8, T_V2, T_V3,
    T_UNICODE, T_ASCII, T_ANGLE,
    T_BOOL_T, T_BOOL_F,
    T_U4_0, T_U4_1, T_U4_B, T_U4_W, T_U4_3,
    T_I4_0, T_I4_B, T_I4_W, T_I4_3,
    T_BOOL_ARY, T_I1_ARY, T_I2_ARY, T_I4_ARY, T_I8_ARY,
    T_U1_ARY, T_U2_ARY, T_U4_ARY, T_U8_ARY,
    T_F4_ARY, T_F8_ARY, T_V2_ARY, T_V3_ARY,
    T_STR_ARY, T_ASC_ARY,
    T_U4_ARY_B, T_U4_ARY_W,
    T_RECORD, T_RECORD_ARY,
)


class ESFReader:
    """Read an ABCE ESF binary file into an ESFNode tree.

    Usage::

        reader = ESFReader(Path("pathfinding.esf"))
        root   = reader.read_root()

    Attributes
    ----------
    tag_names : list[str]
        The footer name table, in original order.  The writer needs this to
        rebuild the footer and to re-encode node-type indices.
    timestamp : int
        Unix timestamp from the file header (preserved for round-trip).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: bytes = path.read_bytes()
        self.pos: int = 0
        self.tag_names: list[str] = []
        self.timestamp: int = 0
        self._read_header()
        self._read_footer()
        # Build reverse map for writer
        self._tag_index: dict[str, int] = {n: i for i, n in enumerate(self.tag_names)}

    # ── Header / footer ───────────────────────────────────────────────────────

    def _read_header(self) -> None:
        magic = self._u32()
        if magic != ESF_MAGIC_ABCE:
            raise ValueError(f"Expected ABCE (0x{ESF_MAGIC_ABCE:X}), got {magic:#x}")
        _zeros             = self._u32()   # always 0x00000000
        self.timestamp     = self._u32()   # Unix timestamp
        self.footer_offset = self._u32()   # absolute offset of footer

    def _read_footer(self) -> None:
        """Read tag-name lookup table from footer (does not advance self.pos)."""
        saved = self.pos
        self.pos = self.footer_offset
        count = self._u16()
        self.tag_names = []
        for _ in range(count):
            length = self._u16()
            name = self.data[self.pos: self.pos + length].decode("ascii")
            self.pos += length
            self.tag_names.append(name)
        self.pos = saved

    # ── Public entry point ────────────────────────────────────────────────────

    def read_root(self) -> ESFNode:
        """Parse the root record starting at byte 16 (just after the header)."""
        self.pos = 16
        node = self._read_node()
        if not isinstance(node, ESFNode):
            raise ValueError(f"Root must be a record node, got {type(node)}")
        return node

    # ── Node dispatch ─────────────────────────────────────────────────────────

    def _read_node(self) -> ESFNode | ESFPrimitive:
        """Read one node (record or primitive) at self.pos."""
        tag = self._u8()

        if tag == T_RECORD:
            return self._read_record(tag)
        if tag == T_RECORD_ARY:
            return self._read_record_array(tag)

        # ── Primitives ────────────────────────────────────────────────────────
        if tag == T_BOOL:
            return ESFPrimitive(tag, bool(self._u8()))
        if tag == T_BOOL_T:
            return ESFPrimitive(tag, True)
        if tag == T_BOOL_F:
            return ESFPrimitive(tag, False)

        if tag == T_I1:
            return ESFPrimitive(tag, self._i8())
        if tag == T_I2:
            return ESFPrimitive(tag, self._i16())
        if tag == T_I4:
            return ESFPrimitive(tag, self._i32())
        if tag == T_I8:
            return ESFPrimitive(tag, self._i64())

        if tag == T_U1:
            return ESFPrimitive(tag, self._u8())
        if tag == T_U2:
            return ESFPrimitive(tag, self._u16())
        if tag == T_U4:
            return ESFPrimitive(tag, self._u32())
        if tag == T_U8:
            return ESFPrimitive(tag, self._u64())

        if tag == T_F4:
            return ESFPrimitive(tag, self._f32())
        if tag == T_F8:
            return ESFPrimitive(tag, self._f64())

        if tag == T_V2:
            return ESFPrimitive(tag, (self._f32(), self._f32()))
        if tag == T_V3:
            return ESFPrimitive(tag, (self._f32(), self._f32(), self._f32()))

        if tag == T_UNICODE:
            return ESFPrimitive(tag, self._unicode())
        if tag == T_ASCII:
            return ESFPrimitive(tag, self._ascii())
        if tag == T_ANGLE:
            return ESFPrimitive(tag, self._u16())

        # ── Compact uint32 variants ───────────────────────────────────────────
        if tag == T_U4_0:
            return ESFPrimitive(tag, 0)
        if tag == T_U4_1:
            return ESFPrimitive(tag, 1)
        if tag == T_U4_B:
            return ESFPrimitive(tag, self._u8())
        if tag == T_U4_W:
            return ESFPrimitive(tag, self._u16())
        if tag == T_U4_3:
            v = int.from_bytes(self.data[self.pos:self.pos + 3], "little")
            self.pos += 3
            return ESFPrimitive(tag, v)

        # ── Compact int32 variants ────────────────────────────────────────────
        if tag == T_I4_0:
            return ESFPrimitive(tag, 0)
        if tag == T_I4_B:
            return ESFPrimitive(tag, self._i8())
        if tag == T_I4_W:
            return ESFPrimitive(tag, self._i16())
        if tag == T_I4_3:
            raw3 = self.data[self.pos:self.pos + 3]
            self.pos += 3
            v = int.from_bytes(raw3, "little", signed=True)
            # Sign-extend from 24 bits
            if raw3[2] & 0x80:
                v |= -0x1000000
            return ESFPrimitive(tag, v)

        # ── Array / blob types ────────────────────────────────────────────────
        if tag == T_BOOL_ARY:
            raw = self._blob()
            return ESFPrimitive(tag, [bool(b) for b in raw], raw)

        if tag == T_I1_ARY:
            raw = self._blob()
            n = len(raw)
            return ESFPrimitive(tag, list(struct.unpack_from(f"<{n}b", raw)), raw)

        if tag == T_I2_ARY:
            raw = self._blob()
            n = len(raw) // 2
            return ESFPrimitive(tag, list(struct.unpack_from(f"<{n}h", raw)), raw)

        if tag == T_I4_ARY:
            raw = self._blob()
            n = len(raw) // 4
            return ESFPrimitive(tag, list(struct.unpack_from(f"<{n}i", raw)), raw)

        if tag == T_I8_ARY:
            raw = self._blob()
            n = len(raw) // 8
            return ESFPrimitive(tag, list(struct.unpack_from(f"<{n}q", raw)), raw)

        if tag == T_U1_ARY:
            raw = self._blob()
            return ESFPrimitive(tag, None, raw)  # raw preserved as-is (bin6)

        if tag == T_U2_ARY:
            raw = self._blob()
            n = len(raw) // 2
            return ESFPrimitive(tag, list(struct.unpack_from(f"<{n}H", raw)), raw)

        if tag == T_U4_ARY:
            raw = self._blob()
            n = len(raw) // 4
            return ESFPrimitive(tag, list(struct.unpack_from(f"<{n}I", raw)), raw)

        if tag == T_U8_ARY:
            raw = self._blob()
            n = len(raw) // 8
            return ESFPrimitive(tag, list(struct.unpack_from(f"<{n}Q", raw)), raw)

        if tag == T_U4_ARY_B:
            raw = self._blob()
            return ESFPrimitive(tag, list(raw), raw)

        if tag == T_U4_ARY_W:
            raw = self._blob()
            n = len(raw) // 2
            return ESFPrimitive(tag, list(struct.unpack_from(f"<{n}H", raw)), raw)

        # Float / vector / string arrays — preserve raw bytes
        if tag in (T_F4_ARY, T_F8_ARY, T_V2_ARY, T_V3_ARY, T_STR_ARY, T_ASC_ARY):
            raw = self._blob()
            return ESFPrimitive(tag, None, raw)

        # All remaining 0x40-0x5F blob types (bin0-bin1f, angle_ary, etc.)
        if 0x40 <= tag <= 0x5F:
            raw = self._blob()
            return ESFPrimitive(tag, None, raw)

        # Unknown — preserve raw bytes from current position to next tag
        # (should not happen with a well-formed file)
        raise ValueError(
            f"Unknown ESF type tag 0x{tag:02X} at offset {self.pos - 1:#x}"
        )

    # ── Record parsers ────────────────────────────────────────────────────────

    def _read_record(self, tag: int) -> ESFNode:
        """Parse a 0x80 single record."""
        tag_idx = self._u16()
        version = self._u8()
        name    = self.tag_names[tag_idx]
        end     = self._u32()   # ABCE: absolute end offset (not varint)
        children: list = []
        while self.pos < end:
            children.append(self._read_node())
        return ESFNode(tag=name, type_tag=tag, version=version, children=children)

    def _read_record_array(self, tag: int) -> ESFNode:
        """Parse a 0x81 record array."""
        tag_idx    = self._u16()
        version    = self._u8()
        name       = self.tag_names[tag_idx]
        _ary_end   = self._u32()   # ABCE: absolute end offset of all items
        count      = self._u32()
        items: list[list] = []
        for _ in range(count):
            end  = self._u32()     # ABCE: absolute end offset of this item
            item: list = []
            while self.pos < end:
                item.append(self._read_node())
            items.append(item)
        return ESFNode(tag=name, type_tag=tag, version=version, children=items)

    # ── Primitives ────────────────────────────────────────────────────────────

    def _u8(self)  -> int:
        v = self.data[self.pos]; self.pos += 1; return v

    def _u16(self) -> int:
        v, = struct.unpack_from("<H", self.data, self.pos); self.pos += 2; return v

    def _u32(self) -> int:
        v, = struct.unpack_from("<I", self.data, self.pos); self.pos += 4; return v

    def _u64(self) -> int:
        v, = struct.unpack_from("<Q", self.data, self.pos); self.pos += 8; return v

    def _i8(self)  -> int:
        v, = struct.unpack_from("<b", self.data, self.pos); self.pos += 1; return v

    def _i16(self) -> int:
        v, = struct.unpack_from("<h", self.data, self.pos); self.pos += 2; return v

    def _i32(self) -> int:
        v, = struct.unpack_from("<i", self.data, self.pos); self.pos += 4; return v

    def _i64(self) -> int:
        v, = struct.unpack_from("<q", self.data, self.pos); self.pos += 8; return v

    def _f32(self) -> float:
        v, = struct.unpack_from("<f", self.data, self.pos); self.pos += 4; return v

    def _f64(self) -> float:
        v, = struct.unpack_from("<d", self.data, self.pos); self.pos += 8; return v

    def _unicode(self) -> str:
        n = self._u16()          # char count
        s = self.data[self.pos: self.pos + n * 2].decode("utf-16-le")
        self.pos += n * 2
        return s

    def _ascii(self) -> str:
        n = self._u16()          # byte count
        s = self.data[self.pos: self.pos + n].decode("ascii")
        self.pos += n
        return s

    def _varint(self) -> int:
        """7-bit continuation varint (little-endian groups, MSB = more)."""
        result = 0
        while True:
            b = self._u8()
            result = (result << 7) | (b & 0x7F)
            if not (b & 0x80):
                break
        return result

    def _varint_end(self) -> int:
        """Read varint relative-length, return absolute end offset."""
        return self.pos + self._varint()

    def _blob(self) -> bytes:
        """Read a uint32-absolute-end-offset-prefixed raw byte block (ABCE format)."""
        end = self._u32()
        raw = self.data[self.pos: end]
        self.pos = end
        return raw
