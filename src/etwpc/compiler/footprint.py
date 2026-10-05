"""
Settlement / slot footprint transplant for pathfinding.esf.

Every vanilla settlement and slot leaves a footprint in the pathfinding grid:
the cells it touches stop being "interior run" cells and become header cells
carrying boundary records of path_type 7 ("slot", the obstacle outline) and
path_type 0 ("passable area", the remaining walkable pieces of the cell). A
reactivated region without these records lets armies enter its settlement but
never leave it (observed 2026-09-06 with Tabuk).

Rather than generate the records, this module copies a clean vanilla footprint
and moves it by a whole number of cells. Because the translation is a multiple
of the cell size, every polyline is clipped by the cell grid exactly as in the
template, so the per-cell headers, passable fractions and edge markers stay
valid. Vertices are appended to the area's vertex table, the boundary
vertex-index list grows, and the cell items around the target are re-split so
that everything outside the block is byte-identical.

Grid item layout (Europe area, verified on vanilla):
    item = [bin(8)  cell header][boundaries: array of (u32 a, u32 b)]
           + optionally [u32 word][u16 pid][bin(12*n) run of following cells]
    an item with boundary records never carries a run; a run cell is 8 header
    bytes + the 4-byte "whole cell passable" word; count field of grid_data =
    boundaries + run cells + items carrying (word, pid).
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass, field

from etwpc.io.esf_types import ESFNode, ESFPrimitive, T_U1_ARY, T_U2, T_U4, T_I4, BoundaryEntry

FIXED = 1 << 20
EDGE_MARKERS = {0, 1, 2, 3}          # path entries <= 3 are cell-edge markers, not vertex ids


@dataclass
class Item:
    hdr: bytes
    bounds: list[tuple[int, int]]
    word: int | None = None
    pid: int | None = None
    run: list[tuple[bytes, int]] = field(default_factory=list)   # (hdr8, word) per trailing cell

    @property
    def ncells(self) -> int:
        return 1 + len(self.run)


@dataclass
class CellSpec:
    hdr: bytes
    bounds: list[tuple[int, int]]
    word: int | None
    pid: int | None


class AreaGrid:
    def __init__(self, area):
        self.area = area
        self.verts_node: ESFNode = area[0]
        self.vlist_prim: ESFPrimitive = area[1]
        self.gd: ESFNode = area[2]
        ch = self.gd.children
        self.ox = ch[0].value / FIXED
        self.oy = ch[1].value / FIXED
        self.cs = ch[4].value / FIXED
        self.cols = ch[5].value
        self.rows = ch[6].value
        self.i2 = list(ch[10].value)
        self.order = list(ch[11].value)[:len(self.i2)]
        self.gc: ESFNode = ch[12]
        self.vlist = list(self.vlist_prim.value)
        self.vx = [it[0].value / FIXED for it in self.verts_node.children]
        self.vy = [it[1].value / FIXED for it in self.verts_node.children]
        self._bnode_proto = None
        self.items: list[Item] = []
        for it in self.gc.children:
            blobs = [c for c in it if isinstance(c, ESFPrimitive) and c.type_tag == T_U1_ARY]
            bnode = next(c for c in it if isinstance(c, ESFNode))
            if self._bnode_proto is None:
                self._bnode_proto = bnode
            bounds = [(b[0].value, b[1].value) for b in bnode.children]
            word = next((c.value for c in it if isinstance(c, ESFPrimitive) and c.type_tag == T_U4), None)
            pid = next((c.value for c in it if isinstance(c, ESFPrimitive) and c.type_tag == T_U2), None)
            run = []
            if len(blobs) > 1:
                raw = blobs[1].raw
                for j in range(len(raw) // 12):
                    run.append((raw[j * 12:j * 12 + 8], struct.unpack_from("<I", raw, j * 12 + 8)[0]))
            self.items.append(Item(blobs[0].raw, bounds, word, pid, run))
        self._index()

    # ── indexing ─────────────────────────────────────────────────────────

    def _index(self):
        self.cell_item: list[tuple[int, int]] = []     # cell -> (item idx, offset)
        for k, it in enumerate(self.items):
            for j in range(it.ncells):
                self.cell_item.append((k, j))
        assert len(self.cell_item) == self.cols * self.rows, (len(self.cell_item), self.cols * self.rows)

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        return int((y - self.oy) / self.cs), int((x - self.ox) / self.cs)

    # ── path id <-> region ───────────────────────────────────────────────

    def region_of(self, pid: int) -> int:
        """regions.esf index of a region path id. Not i2[pid]: grid_data[11] starts with
        a permutation of 1..n giving each path id's 1-based slot in i2 (deep_dive 9.10;
        vanilla swaps e.g. palestine/tripoli and rotates gibraltar/khiva/spain)."""
        return self.i2[self.order[pid] - 1]

    def pid_of(self, region_idx: int) -> int:
        return self.order.index(self.i2.index(region_idx) + 1)

    def kind(self, r: int, c: int):
        """('run', pid) | ('hdr0', pid) zero-record header | ('hdr', None) records."""
        k, j = self.cell_item[r * self.cols + c]
        it = self.items[k]
        if j > 0:
            return ("run", it.pid)
        if it.bounds:
            return ("hdr", None)
        return ("hdr0", it.pid)

    def spec(self, r: int, c: int) -> CellSpec:
        k, j = self.cell_item[r * self.cols + c]
        it = self.items[k]
        if j == 0:
            return CellSpec(it.hdr, list(it.bounds), it.word, it.pid)
        h, w = it.run[j - 1]
        return CellSpec(h, [], w, it.pid)

    # ── template extraction ──────────────────────────────────────────────

    def template_block(self, x: float, y: float, radius: int, keep_types=(0, 7)):
        """Header cells within `radius` of (x, y): (dr, dc, hdr, records, word).
        records = (packed_a, path_type, pid, entries) with entries decoded."""
        r0, c0 = self.cell_of(x, y)
        pidc = Counter()
        raw = []
        for r in range(r0 - radius, r0 + radius + 1):
            for c in range(c0 - radius, c0 + radius + 1):
                k, j = self.cell_item[r * self.cols + c]
                if j != 0:
                    continue
                it = self.items[k]
                recs = []
                for a, b in it.bounds:
                    be = BoundaryEntry.from_packed(a, b)
                    if be.path_type == 0:
                        pidc[be.path_id] += 1
                    n = self.vlist[be.vertex_index]
                    ents = self.vlist[be.vertex_index + 1: be.vertex_index + 1 + n]
                    recs.append((a, be.path_type, be.path_id, ents))
                raw.append((r - r0, c - c0, it.hdr, recs, it.word if not it.bounds else None))
        local_pid = pidc.most_common(1)[0][0]
        block = []
        for dr, dc, hdr, recs, word in raw:
            kept = [(a, t, p, e) for a, t, p, e in recs if t in keep_types and p == local_pid]
            dropped = [(t, p) for a, t, p, e in recs if not (t in keep_types and p == local_pid)]
            block.append((dr, dc, hdr, kept, word, dropped))
        return local_pid, block

    def block_is_clean(self, x: float, y: float, radius: int, pid: int, reserved: set) -> bool:
        r0, c0 = self.cell_of(x, y)
        for r in range(r0 - radius, r0 + radius + 1):
            for c in range(c0 - radius, c0 + radius + 1):
                if not (0 <= r < self.rows and 0 <= c < self.cols):
                    return False
                k, p = self.kind(r, c)
                if k == "hdr" or p != pid or (r, c) in reserved:
                    return False
        return True

    # ── transplant ───────────────────────────────────────────────────────

    def transplant(self, template_xy, target_xy, radius: int, dst_pid: int, keep_types=(0, 7),
                   src: "AreaGrid | None" = None) -> dict:
        """Copy the footprint around template_xy (in `src`, default this grid) to
        target_xy. Geometry is in world units and every grid shares the same
        2-unit cell lattice (all origins are even), so a template from another
        grid lands exactly as long as target = template + whole cells."""
        S = src or self
        tx, ty = template_xy
        gx, gy = target_xy
        dx, dy = gx - tx, gy - ty
        dc, dr = round(dx / self.cs), round(dy / self.cs)
        assert abs(dc * self.cs - dx) < 1e-3 and abs(dr * self.cs - dy) < 1e-3, "target must be template + whole cells"
        assert abs(((self.ox - S.ox) / self.cs) % 1) < 1e-6 and abs(((self.oy - S.oy) / self.cs) % 1) < 1e-6, \
            "source and destination grids are not on the same cell lattice"
        local_pid, block = S.template_block(tx, ty, radius, keep_types)
        r0, c0 = self.cell_of(gx, gy)
        vmap: dict[int, int] = {}
        new_specs: dict[int, CellSpec] = {}
        n_rec = 0
        dropped_total = Counter()
        for bdr, bdc, hdr, recs, word, dropped in block:
            r, c = r0 + bdr, c0 + bdc
            for t, p in dropped:
                dropped_total[t] += 1
            k, p = self.kind(r, c)
            assert k != "hdr" and p == dst_pid, f"target cell r{r} c{c} is {k}/{p}, expected interior of pid {dst_pid}"
            bounds = []
            for a, t, p, ents in recs:
                new_ents = []
                for e in ents:
                    if e in EDGE_MARKERS:
                        new_ents.append(e)
                    else:
                        if e not in vmap:
                            vmap[e] = self._add_vertex(S.vx[e] + dx, S.vy[e] + dy)
                        new_ents.append(vmap[e])
                vi = len(self.vlist)
                self.vlist.append(len(new_ents))
                self.vlist.extend(new_ents)
                b = ((dst_pid & 0x3FF) << 22) | (vi & 0x3FFFFF)
                bounds.append((a, b))
                n_rec += 1
            new_specs[r * self.cols + c] = CellSpec(hdr, bounds, None if bounds else word, None if bounds else dst_pid)
        self._apply(new_specs)
        return {"cells": len(new_specs), "records": n_rec, "vertices": len(vmap),
                "dropped": dict(dropped_total), "template_pid": local_pid, "offset_cells": (dr, dc)}

    def _add_vertex(self, x: float, y: float) -> int:
        self.verts_node.children.append([ESFPrimitive(T_I4, int(round(x * FIXED)), b""),
                                         ESFPrimitive(T_I4, int(round(y * FIXED)), b"")])
        self.vx.append(x)
        self.vy.append(y)
        return len(self.vx) - 1

    def _apply(self, new_specs: dict[int, CellSpec]):
        """Re-split every item that covers a replaced cell; leave all others untouched."""
        by_item: dict[int, list[int]] = {}
        for cell in new_specs:
            k, _ = self.cell_item[cell]
            by_item.setdefault(k, []).append(cell)
        new_items: list[Item] = []
        cell = 0
        for k, it in enumerate(self.items):
            if k not in by_item:
                new_items.append(it)
                cell += it.ncells
                continue
            specs = []
            for j in range(it.ncells):
                idx = cell + j
                if idx in new_specs:
                    specs.append(new_specs[idx])
                elif j == 0:
                    specs.append(CellSpec(it.hdr, list(it.bounds), it.word, it.pid))
                else:
                    h, w = it.run[j - 1]
                    specs.append(CellSpec(h, [], w, it.pid))
            cell += it.ncells
            i = 0
            while i < len(specs):
                s = specs[i]
                if s.bounds:
                    new_items.append(Item(s.hdr, s.bounds))
                    i += 1
                    continue
                item = Item(s.hdr, [], s.word, s.pid)
                i += 1
                while i < len(specs) and not specs[i].bounds and specs[i].pid == s.pid:
                    item.run.append((specs[i].hdr, specs[i].word))
                    i += 1
                new_items.append(item)
        self.items = new_items
        self._index()

    # ── serialisation ────────────────────────────────────────────────────

    def serialize(self):
        proto = self._bnode_proto
        children = []
        for it in self.items:
            bnode = ESFNode(tag=proto.tag, type_tag=proto.type_tag, version=proto.version)
            bnode.children = [[ESFPrimitive(T_U4, a, b""), ESFPrimitive(T_U4, b, b"")] for a, b in it.bounds]
            item = [ESFPrimitive(T_U1_ARY, None, it.hdr), bnode]
            if it.word is not None:
                raw = b"".join(h + struct.pack("<I", w) for h, w in it.run)
                item += [ESFPrimitive(T_U4, it.word, b""), ESFPrimitive(T_U2, it.pid, b""), ESFPrimitive(T_U1_ARY, None, raw)]
            children.append(item)
        self.gc.children = children
        self.vlist_prim.value = list(self.vlist)
        self.vlist_prim.raw = b""
        count = sum(len(it.bounds) for it in self.items) + sum(len(it.run) for it in self.items) \
            + sum(1 for it in self.items if it.word is not None)
        cnt_prim = self.gd.children[7]
        cnt_prim.value, cnt_prim.raw = count, b""
        return count

    def sequence_index(self) -> list[int]:
        """Per cell: its position in the flat sequence the engine builds from the grid
        (boundary records + run cells + items carrying a word, cumulative). startpos's
        CAMPAIGN_PATHFINDER OBSTACLE_BASE_GRID_NODE ids are exactly these positions
        (verified on all 9,862 vanilla nodes), so they must be remapped after any edit
        that changes the sequence length before a cell."""
        seq = []
        pos = 0
        for it in self.items:
            seq.append(pos)
            pos += len(it.bounds) + (1 if it.word is not None else 0)
            for _ in it.run:
                seq.append(pos)
                pos += 1
        return seq

    def footprint_types_near(self, x: float, y: float, radius: int = 2) -> Counter:
        r0, c0 = self.cell_of(x, y)
        cnt = Counter()
        for r in range(r0 - radius, r0 + radius + 1):
            for c in range(c0 - radius, c0 + radius + 1):
                k, j = self.cell_item[r * self.cols + c]
                if j == 0:
                    for a, b in self.items[k].bounds:
                        cnt[BoundaryEntry.from_packed(a, b).path_type] += 1
        return cnt


def snap(template_xy, desired_xy, cs: float = 2.0):
    """Nearest point to `desired` that is `template` plus a whole number of cells."""
    tx, ty = template_xy
    gx, gy = desired_xy
    return (tx + cs * round((gx - tx) / cs), ty + cs * round((gy - ty) / cs))


def find_clean_target(grid: AreaGrid, template_xy, desired_xy, radius: int, pid: int, reserved: set,
                      inside, max_ring: int = 6):
    """Snapped position nearest `desired` whose block is all interior cells of `pid`,
    not reserved, and whose centre satisfies `inside(x, y)`. Reserves the block."""
    base = snap(template_xy, desired_xy, grid.cs)
    cands = sorted(((i * i + j * j), i, j) for i in range(-max_ring, max_ring + 1) for j in range(-max_ring, max_ring + 1))
    for _, i, j in cands:
        x, y = base[0] + i * grid.cs, base[1] + j * grid.cs
        if inside(x, y) and grid.block_is_clean(x, y, radius, pid, reserved):
            r0, c0 = grid.cell_of(x, y)
            for r in range(r0 - radius - 1, r0 + radius + 2):
                for c in range(c0 - radius - 1, c0 + radius + 2):
                    reserved.add((r, c))
            return (x, y)
    raise RuntimeError(f"no clean block of radius {radius} near {desired_xy}")
