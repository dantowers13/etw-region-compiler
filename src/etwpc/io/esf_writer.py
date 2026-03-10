"""
ABCE ESF binary writer — ETWPC-12.

Serialises an ESFNode tree (as produced by ESFReader) back to the exact
ABCE binary format.  The round-trip test (ETWPC-26) requires byte-for-byte
identity with the original file.
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


class ESFWriter:
    """Serialise an ESFNode tree back to ABCE ESF binary.

    Parameters
    ----------
    root : ESFNode
        The root node returned by ESFReader.read_root().
    tag_names : list[str]
        The footer name table from ESFReader.tag_names — order must be preserved.
    timestamp : int
        The Unix timestamp from the original file header.
    """

    def __init__(self, root: ESFNode, tag_names: list[str], timestamp: int = 0) -> None:
        self.root      = root
        self.tag_names = tag_names
        self.timestamp = timestamp
        self._tag_index: dict[str, int] = {n: i for i, n in enumerate(tag_names)}

    # ── Public API ────────────────────────────────────────────────────────────

    def write(self, path: Path) -> None:
        """Serialise to *path*."""
        path.write_bytes(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialise to a bytes object."""
        body    = self._encode_node(self.root, offset=16)
        footer  = self._encode_footer()
        footer_offset = 16 + len(body)          # header is always 16 bytes

        header = struct.pack("<IIII",
            ESF_MAGIC_ABCE,
            0,                                  # zeros
            self.timestamp,
            footer_offset,
        )
        return header + body + footer

    # ── Footer ────────────────────────────────────────────────────────────────

    def _encode_footer(self) -> bytes:
        parts = [struct.pack("<H", len(self.tag_names))]
        for name in self.tag_names:
            enc = name.encode("ascii")
            parts.append(struct.pack("<H", len(enc)))
            parts.append(enc)
        return b"".join(parts)

    # ── Node encoder ──────────────────────────────────────────────────────────

    def _encode_node(self, node: ESFNode | ESFPrimitive, offset: int) -> bytes:
        if isinstance(node, ESFNode):
            return self._encode_record(node, offset)
        return self._encode_primitive(node, offset)

    def _encode_record(self, node: ESFNode, offset: int) -> bytes:
        tag_idx = self._tag_index[node.tag]

        if node.type_tag == T_RECORD:
            # Header: tag(1) + u16(2) + u8(1) + u32(abs_end)(4) = 8 bytes
            # abs_end is the absolute file offset of the byte after all children.
            children_start = offset + 8
            children_bytes = self._encode_children(node.children, children_start)
            abs_end = offset + 8 + len(children_bytes)
            header  = struct.pack("<BHBI", node.type_tag, tag_idx, node.version, abs_end)
            return header + children_bytes

        else:  # T_RECORD_ARY
            # Header: tag(1) + u16(2) + u8(1) + u32(abs_end)(4) + u32(count)(4) = 12 bytes
            items_start = offset + 12
            all_items   = self._encode_array_items(node.children, items_start)
            abs_end     = offset + 12 + len(all_items)
            count       = len(node.children)
            header      = struct.pack("<BHBII", node.type_tag, tag_idx, node.version, abs_end, count)
            return header + all_items

    def _encode_children(self, children: list, offset: int) -> bytes:
        """Encode a flat list of child nodes, tracking absolute offset as we go."""
        result = b""
        for c in children:
            result += self._encode_node(c, offset + len(result))
        return result

    def _encode_array_items(self, items: list, base_offset: int) -> bytes:
        """Encode T_RECORD_ARY items, each prefixed by a uint32 absolute end offset.

        ABCE format: item header = u32(abs_end), body follows immediately.
        abs_end = (position of u32) + 4 + len(body).
        """
        result = b""
        cur_offset = base_offset
        for item in items:
            # Body starts after the 4-byte abs_end u32
            item_body    = self._encode_children(item, cur_offset + 4)
            item_abs_end = cur_offset + 4 + len(item_body)
            item_bytes   = struct.pack("<I", item_abs_end) + item_body
            result      += item_bytes
            cur_offset  += len(item_bytes)
        return result

    def _encode_primitive(self, p: ESFPrimitive, offset: int) -> bytes:
        t = p.type_tag
        v = p.value

        if t == T_BOOL:     return bytes([T_BOOL, 1 if v else 0])
        if t == T_BOOL_T:   return bytes([T_BOOL_T])
        if t == T_BOOL_F:   return bytes([T_BOOL_F])

        if t == T_I1:       return struct.pack("<Bb", t, v)
        if t == T_I2:       return struct.pack("<Bh", t, v)
        if t == T_I4:       return struct.pack("<Bi", t, v)
        if t == T_I8:       return struct.pack("<Bq", t, v)

        if t == T_U1:       return struct.pack("<BB", t, v)
        if t == T_U2:       return struct.pack("<BH", t, v)
        if t == T_U4:       return struct.pack("<BI", t, v)
        if t == T_U8:       return struct.pack("<BQ", t, v)

        if t == T_F4:       return struct.pack("<Bf", t, v)
        if t == T_F8:       return struct.pack("<Bd", t, v)

        if t == T_V2:       return struct.pack("<Bff", t, *v)
        if t == T_V3:       return struct.pack("<Bfff", t, *v)

        if t == T_UNICODE:
            enc = v.encode("utf-16-le")
            return struct.pack("<BH", t, len(v)) + enc

        if t == T_ASCII:
            enc = v.encode("ascii")
            return struct.pack("<BH", t, len(enc)) + enc

        if t == T_ANGLE:    return struct.pack("<BH", t, v)

        # Compact uint32
        if t == T_U4_0:     return bytes([T_U4_0])
        if t == T_U4_1:     return bytes([T_U4_1])
        if t == T_U4_B:     return struct.pack("<BB", t, v)
        if t == T_U4_W:     return struct.pack("<BH", t, v)
        if t == T_U4_3:
            return bytes([t]) + v.to_bytes(3, "little")

        # Compact int32
        if t == T_I4_0:     return bytes([T_I4_0])
        if t == T_I4_B:     return struct.pack("<Bb", t, v)
        if t == T_I4_W:     return struct.pack("<Bh", t, v)
        if t == T_I4_3:
            # Low 24 bits, little-endian (original signed value)
            raw3 = (v & 0xFFFFFF).to_bytes(3, "little")
            return bytes([t]) + raw3

        # Array / blob types — ABCE: u32(abs_end) prefix, not varint.
        # abs_end = offset + 1 (tag) + 4 (u32) + len(raw) = offset + 5 + len(raw)
        def blob(raw: bytes) -> bytes:
            return self._blob(raw, offset)

        if t == T_BOOL_ARY:
            raw = p.raw if p.raw else bytes(1 if x else 0 for x in v)
            return bytes([t]) + blob(raw)

        if t == T_I1_ARY:
            raw = p.raw if p.raw else struct.pack(f"<{len(v)}b", *v)
            return bytes([t]) + blob(raw)

        if t == T_I2_ARY:
            raw = p.raw if p.raw else struct.pack(f"<{len(v)}h", *v)
            return bytes([t]) + blob(raw)

        if t == T_I4_ARY:
            raw = p.raw if p.raw else struct.pack(f"<{len(v)}i", *v)
            return bytes([t]) + blob(raw)

        if t == T_I8_ARY:
            raw = p.raw if p.raw else struct.pack(f"<{len(v)}q", *v)
            return bytes([t]) + blob(raw)

        if t == T_U1_ARY:
            return bytes([t]) + blob(p.raw)

        if t == T_U2_ARY:
            raw = p.raw if p.raw else struct.pack(f"<{len(v)}H", *v)
            return bytes([t]) + blob(raw)

        if t == T_U4_ARY:
            raw = p.raw if p.raw else struct.pack(f"<{len(v)}I", *v)
            return bytes([t]) + blob(raw)

        if t == T_U8_ARY:
            raw = p.raw if p.raw else struct.pack(f"<{len(v)}Q", *v)
            return bytes([t]) + blob(raw)

        if t == T_U4_ARY_B:
            raw = p.raw if p.raw else bytes(v)
            return bytes([t]) + blob(raw)

        if t == T_U4_ARY_W:
            raw = p.raw if p.raw else struct.pack(f"<{len(v)}H", *v)
            return bytes([t]) + blob(raw)

        # Float / vector / string arrays — always raw (not decoded)
        if t in (T_F4_ARY, T_F8_ARY, T_V2_ARY, T_V3_ARY, T_STR_ARY, T_ASC_ARY):
            return bytes([t]) + blob(p.raw)

        # All remaining blob types (bin0-bin1f etc.)
        if 0x40 <= t <= 0x5F:
            return bytes([t]) + blob(p.raw)

        raise ValueError(f"Cannot encode unknown type tag 0x{t:02X}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _blob(self, raw: bytes, tag_offset: int) -> bytes:
        """ABCE blob encoding: u32(abs_end) + raw bytes.

        tag_offset is the absolute file offset of the type tag byte that
        precedes the u32.  abs_end = tag_offset + 1 (tag) + 4 (u32) + len(raw).
        """
        abs_end = tag_offset + 1 + 4 + len(raw)
        return struct.pack("<I", abs_end) + raw
