"""
ABCE ESF binary reader.

Implement per ETWPC-11. Header and footer parsing are implemented;
node parsing raises NotImplementedError until ETWPC-11 is complete.
"""
from __future__ import annotations
import struct
from pathlib import Path
from .esf_types import ESFNode, ESF_MAGIC_ABCE


class ESFReader:
    """Read an ABCE ESF binary file into an ESFNode tree."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = path.read_bytes()
        self.pos = 0
        self.tag_names: list[str] = []
        self._read_header()
        self._read_footer()

    # ── Header ────────────────────────────────────────────────────────────────
    def _read_header(self) -> None:
        magic = self._u32()
        if magic != ESF_MAGIC_ABCE:
            raise ValueError(f"Expected ABCE (0xABCE), got {magic:#x} in {self.path}")
        _zeros          = self._u32()   # always 0x00000000
        _timestamp      = self._u32()   # Unix timestamp
        self.footer_offset = self._u32()

    def _read_footer(self) -> None:
        """Read tag name lookup table from footer."""
        saved_pos = self.pos
        self.pos = self.footer_offset
        count = self._u16()
        self.tag_names = []
        for _ in range(count):
            length = self._u16()
            name = self.data[self.pos : self.pos + length].decode("ascii")
            self.pos += length
            self.tag_names.append(name)
        self.pos = saved_pos

    # ── Primitives ────────────────────────────────────────────────────────────
    def _u8(self)  -> int:   v, = struct.unpack_from("<B", self.data, self.pos); self.pos += 1; return v
    def _u16(self) -> int:   v, = struct.unpack_from("<H", self.data, self.pos); self.pos += 2; return v
    def _u32(self) -> int:   v, = struct.unpack_from("<I", self.data, self.pos); self.pos += 4; return v
    def _i32(self) -> int:   v, = struct.unpack_from("<i", self.data, self.pos); self.pos += 4; return v
    def _f32(self) -> float: v, = struct.unpack_from("<f", self.data, self.pos); self.pos += 4; return v
    def _f64(self) -> float: v, = struct.unpack_from("<d", self.data, self.pos); self.pos += 8; return v

    def _uintvar(self) -> int:
        """Variable-length unsigned integer (7-bit continuation encoding)."""
        result = 0
        while True:
            b = self._u8()
            result = (result << 7) | (b & 0x7F)
            if not (b & 0x80):
                break
        return result

    def _ca_ascii(self) -> str:
        length = self._u16()
        s = self.data[self.pos : self.pos + length].decode("ascii")
        self.pos += length
        return s

    def _ca_unicode(self) -> str:
        length = self._u16()
        s = self.data[self.pos : self.pos + length * 2].decode("utf-16-le")
        self.pos += length * 2
        return s

    # ── Node parsing ──────────────────────────────────────────────────────────
    def read_root(self) -> ESFNode:
        """Parse the root node from current position."""
        raise NotImplementedError("ETWPC-11: implement ESF node parser")

    def read_node(self) -> ESFNode:
        """Parse a single node (record or primitive)."""
        raise NotImplementedError("ETWPC-11: implement ESF node parser")
