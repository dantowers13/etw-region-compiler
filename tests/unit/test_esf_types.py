"""
Unit tests for ESF data types and binary encoding.
These run without any game files.
"""
import pytest
from etwpc.io.esf_types import CellIdEntry, BoundaryEntry


class TestCellIdEntry:
    def test_round_trip_basic(self):
        """Pack and unpack a cell_id_coords entry."""
        entry = CellIdEntry(row=42, col=137, cell_id=99)
        packed_pos, packed_id = entry.to_packed()
        recovered = CellIdEntry.from_packed(packed_pos, packed_id)
        assert recovered.row     == entry.row
        assert recovered.col     == entry.col
        assert recovered.cell_id == entry.cell_id

    def test_row_col_packing(self):
        """Verify bit layout: row in upper 16 bits, col in lower 16 bits."""
        entry = CellIdEntry(row=1, col=0, cell_id=0)
        packed_pos, _ = entry.to_packed()
        assert packed_pos == 0x00010000

        entry2 = CellIdEntry(row=0, col=1, cell_id=0)
        packed_pos2, _ = entry2.to_packed()
        assert packed_pos2 == 0x00000001

    def test_max_values(self):
        """Max row/col are 16-bit values (0..65535)."""
        entry = CellIdEntry(row=65535, col=65535, cell_id=0xFFFFFFFF)
        packed_pos, packed_id = entry.to_packed()
        recovered = CellIdEntry.from_packed(packed_pos, packed_id)
        assert recovered.row == 65535
        assert recovered.col == 65535
        assert recovered.cell_id == 0xFFFFFFFF


class TestBoundaryEntry:
    def test_round_trip(self):
        """Pack and unpack a boundary record."""
        entry = BoundaryEntry(path_id=5, vertex_index=1023, passable_part=1, unknown2=0, path_type=3)
        u1, u2 = entry.to_packed()
        recovered = BoundaryEntry.from_packed(u1, u2)
        assert recovered.path_id       == entry.path_id
        assert recovered.vertex_index  == entry.vertex_index
        assert recovered.passable_part == entry.passable_part
        assert recovered.path_type     == entry.path_type

    def test_zero_entry(self):
        """All-zero entry round trips cleanly."""
        entry = BoundaryEntry()
        u1, u2 = entry.to_packed()
        recovered = BoundaryEntry.from_packed(u1, u2)
        assert recovered.path_id == 0
        assert recovered.vertex_index == 0
