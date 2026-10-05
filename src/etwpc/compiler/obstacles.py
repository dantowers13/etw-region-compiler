"""
Fort-obstacle cloning for startpos.esf.

Why this exists
---------------
A settlement in a reactivated void region is a "trap cell": armies can enter it
and never leave (observed 2026-09-06; the openETW team describe the same vanilla
defect at Montagnais/Labrador and fix it under "obstacle creation / deletion").

The fix is NOT static footprint records in pathfinding.esf. Measured against the
Lord mod's URR (Ultima Ratio Regum) startpos, which is the shipped precedent:

  * URR's pathfinding.esf is byte-identical to vanilla apart from one byte.
  * URR has 85 fort obstacles vs vanilla's 15. 70 of them are backed by synthetic
    fort objects parked at a sentinel position (-342.47, -151.94) with an empty
    name key, while the obstacle's bbox sits on the new settlement.
  * One such obstacle is centred at (266, 201), 0.07 world units from URR's
    wilderness_arabia capital.

So runtime navigability comes from a FORT_OBSTACLE in
startpos CAMPAIGN_PATHFINDER/PATHFINDING_GRID[n], not from the grid file.

Anatomy of one obstacle (decoded from URR's Tabuk obstacle, id 962718136)
-------------------------------------------------------------------------
For a settlement at world (X, Y) URR lays down a 5-row x 6-col block of cells:

  OBSTACLE_BOUNDARIES   30 new entries, one per cell, each
                        [n, (a,b) * n, cellid = row<<16|col, 0]
  OBSTACLE_BASE_GRID_NODE  30 new nodes, each
                        [u32 seq_index_of_cell, u32 1,
                         MANAGED_OBSTACLE_BOUNDARY [[boundary_entry_idx, 0, True, [id+1, 1]]],
                         MANAGED_OBSTACLE_BOUNDARY [[True, [id+1, 1]]]]
                        seq_index is the cell's position in the pathfinding grid's
                        flat record/empty-cell sequence (see footprint.sequence_index)
  child[5] pair list    30 new (cellid, node_index) pairs
  FORT_OBSTACLE         the OBSTACLE record; its BOUNDARIES slot[1] lists the 30
                        entry indices, high bit 0x80000000 set on the 18 ring cells
                        and clear on the 12 interior cells
  OBSTACLE_LISTS[4]     the obstacle id appended to the parallel fort id list

Because the pathfinding grid is untouched, every seq_index and every entry word in a
donor startpos is still valid in ours, so donor data is copied verbatim and only ids
and array indices are remapped. The unit of copying is the cell, not the obstacle:
see ObstacleCloner.
"""

from __future__ import annotations

from dataclasses import dataclass

from etwpc.io.esf_types import ESFNode, ESFPrimitive, T_U4

FIXED = 1 << 20
RING_FLAG = 0x80000000


def _find(nd, tag):
    st = [nd]
    while st:
        x = st.pop(0)
        if isinstance(x, ESFNode):
            if x.tag == tag:
                return x
            st.extend(x.children)
        elif isinstance(x, list):
            st.extend(x)
    return None


def _deep_copy(n):
    import copy as _c
    if isinstance(n, ESFPrimitive):
        return ESFPrimitive(type_tag=n.type_tag, value=_c.deepcopy(n.value),
                            raw=bytes(n.raw) if n.raw else b"")
    if isinstance(n, ESFNode):
        m = ESFNode(tag=n.tag, type_tag=n.type_tag, version=n.version)
        m.children = [_deep_copy(c) for c in n.children]
        return m
    if isinstance(n, list):
        return [_deep_copy(c) for c in n]
    return _c.deepcopy(n)


def _set_int(p: ESFPrimitive, v: int) -> None:
    p.type_tag = T_U4
    p.value, p.raw = int(v), b""


def _set_list(p: ESFPrimitive, vals) -> None:
    p.value, p.raw = list(vals), b""


@dataclass
class BoundaryEntry:
    """One OBSTACLE_BOUNDARIES record: n pairs, then the packed cell id, then 0."""
    pairs: list[int]
    cellid: int
    trailer: int

    @property
    def cell(self) -> tuple[int, int]:
        return self.cellid >> 16, self.cellid & 0xFFFF

    def emit(self) -> list[int]:
        return [len(self.pairs) // 2] + self.pairs + [self.cellid, self.trailer]


class ObstacleSystem:
    """The obstacle half of one startpos PATHFINDING_GRID."""

    def __init__(self, grid: ESFNode):
        self.grid = grid
        self.pair_prim: ESFPrimitive = grid[5]
        self.nodes: ESFNode = _find(grid, "OBSTACLE_BASE_GRID_NODE")
        self.lists: ESFNode = _find(grid, "OBSTACLE_LISTS")
        self.forts: ESFNode = _find(self.lists, "FORT_OBSTACLE")
        self.fort_ids_prim: ESFPrimitive = self.lists.children[4]
        self.manager: ESFNode = _find(grid, "OBSTACLE_BOUNDARY_MANAGER").children[0]
        self.blob_prim: ESFPrimitive = _find(grid, "OBSTACLE_BOUNDARIES").children[0]
        self.entries = self._parse(list(self.blob_prim.value))
        p = list(self.pair_prim.value)
        self.pairs = [(p[i], p[i + 1]) for i in range(0, len(p), 2)]

    @staticmethod
    def _parse(v: list[int]) -> list[BoundaryEntry]:
        out, p = [], 0
        while p < len(v):
            n = v[p]
            out.append(BoundaryEntry(v[p + 1: p + 1 + 2 * n], v[p + 1 + 2 * n], v[p + 2 + 2 * n]))
            p += 3 + 2 * n
        return out

    def obstacle(self, oid: int):
        for it in self.forts.children:
            if it[1].value == oid:
                return it
        return None

    def obstacle_bbox(self, item) -> tuple[float, float, float, float]:
        ob = _find(item, "OBSTACLE")
        n = [c.value for c in ob.children if isinstance(c, ESFPrimitive) and c.type_tag == 0x04]
        return n[0] / FIXED, n[1] / FIXED, n[2] / FIXED, n[3] / FIXED

    def node_for_cell(self, cellid: int):
        for packed, idx in self.pairs:
            if packed == cellid:
                return idx
        return None

    def manager_ids(self) -> set[int]:
        """Ids registered in OBSTACLE_BOUNDARY_MANAGER. The manager is read before the
        grid nodes and populates the map the nodes look up in, so every id a node
        references must appear here or campaign load dereferences a miss."""
        out = set()
        for it in self.manager.children:
            for c in it:
                if isinstance(c, ESFPrimitive) and isinstance(c.value, (list, tuple)):
                    v = list(c.value)
                    out.update(v[k] for k in range(0, len(v), 2))
        return out

    def manager_lists(self) -> set[tuple[int, ...]]:
        """The (id, flag, id, flag, ...) lists registered in OBSTACLE_BOUNDARY_MANAGER."""
        out = set()
        for it in self.manager.children:
            for c in it:
                if isinstance(c, ESFPrimitive) and isinstance(c.value, (list, tuple)):
                    out.add(tuple(c.value))
        return out

    def node_lists(self) -> list[tuple[int, ...]]:
        """Every distinct boundary list the grid nodes reference, in first-seen order."""
        seen: dict[tuple[int, ...], None] = {}
        for node in self.nodes.children:
            for ch in node:
                if isinstance(ch, ESFNode) and ch.tag == "MANAGED_OBSTACLE_BOUNDARY":
                    for e in ch.children:
                        for c in e:
                            if isinstance(c, ESFPrimitive) and isinstance(c.value, (list, tuple)):
                                seen.setdefault(tuple(c.value), None)
        return list(seen)

    def sync_manager(self) -> int:
        """Register every node boundary list that the manager lacks; returns how many.

        Rule (holds on all 7 grids of vanilla and URR): the manager is exactly the
        set of distinct (id, flag, ...) lists referenced by the grid nodes, and the
        runtime map is keyed by the whole list, not by single ids. A cell covered by
        two obstacles references a combined list such as [a+1, 1, b+1, 1], which must
        be registered as one entry or campaign load misses it (Empire.exe+0x6a7a16)."""
        have = self.manager_lists()
        added = 0
        for lst in self.node_lists():
            if lst in have:
                continue
            proto = _deep_copy(self.manager.children[0])
            for c in proto:
                if isinstance(c, ESFPrimitive) and isinstance(c.value, (list, tuple)):
                    _set_list(c, lst)
            self.manager.children.append(proto)
            have.add(lst)
            added += 1
        return added

    def node_boundary_ids(self, node) -> set[int]:
        out = set()
        for ch in node:
            if isinstance(ch, ESFNode) and ch.tag == "MANAGED_OBSTACLE_BOUNDARY":
                for e in ch.children:
                    for c in e:
                        if isinstance(c, ESFPrimitive) and isinstance(c.value, (list, tuple)):
                            v = list(c.value)
                            out.update(v[k] for k in range(0, len(v), 2))
        return out

    def flush(self) -> None:
        blob = []
        for e in self.entries:
            blob += e.emit()
        _set_list(self.blob_prim, blob)
        flat = []
        for packed, idx in self.pairs:
            flat += [packed, idx]
        _set_list(self.pair_prim, flat)


def _mobs(node) -> list[ESFNode]:
    """A grid node's two MANAGED_OBSTACLE_BOUNDARY blocks: [0] holds one entry per
    boundary entry in the cell ([entry_index, k, True, id list]), [1] the set of
    distinct id lists ([True, id list])."""
    return [c for c in node if isinstance(c, ESFNode) and c.tag == "MANAGED_OBSTACLE_BOUNDARY"]


class ObstacleCloner:
    """Copies donor fort obstacles into one destination grid, cell by cell.

    Measured on vanilla and URR (all 7 grids): every OBSTACLE_BOUNDARIES entry is
    listed by exactly one grid node, the node of the entry's own cell; entries owned
    by an obstacle slot carry that obstacle's id, and the rest are the geometry of
    cells where obstacles overlap (lists of 2+ ids). So the unit of copying is the
    CELL: a cloned obstacle is only consistent once every cell it touches carries
    the donor node's entries for it, combined ones included. Copying obstacle by
    obstacle (the old clone_obstacle) dropped the second obstacle's entries in a
    shared cell and kept donor entry indices pointing at unrelated cells, which
    crashed campaign load (Empire.exe+0x6f6b00 / +0x51470, deep_dive 8.14).

    Entry words reference pathfinding.esf data (the top bits of the second word are
    the cell's pathfinding region label), which is identical in donor and dst, so
    entries are copied verbatim; only entry indices and boundary ids are remapped.
    Usage: clone() each donor obstacle, then finish() once per grid."""

    def __init__(self, dst: ObstacleSystem, src: ObstacleSystem):
        self.dst, self.src = dst, src
        self.id_map: dict[int, int] = {}         # donor boundary id (fort id + 1) -> ours
        self.entry_map: dict[int, int] = {}      # donor entry index -> ours
        self.cells: set[int] = set()             # donor cells touched by cloned obstacles
        self.pending: list[tuple] = []           # (item, donor slot refs) awaiting finish()

    def clone(self, src_id: int, new_id: int) -> dict:
        src, dst = self.src, self.dst
        src_item = src.obstacle(src_id)
        if src_item is None:
            raise KeyError(f"donor has no fort obstacle {src_id}")
        slots = [list(slot[0].value) for slot in _find(src_item, "OBSTACLE").children[0].children]
        for refs in slots:
            for raw in refs:
                self.cells.add(src.entries[raw & ~RING_FLAG].cellid)
        item = _deep_copy(src_item)
        _set_int(item[1], new_id)
        ob = _find(item, "OBSTACLE")
        for slot in ob.children[5].children:
            for c in slot:
                if isinstance(c, ESFPrimitive) and isinstance(c.value, (list, tuple)):
                    _set_list(c, [new_id + 1 if v == src_id + 1 else v for v in c.value])
        dst.forts.children.append(item)
        _set_list(dst.fort_ids_prim, list(dst.fort_ids_prim.value) + [new_id])
        self.id_map[src_id + 1] = new_id + 1
        self.pending.append((item, slots))
        x0, y0, x1, y1 = dst.obstacle_bbox(item)
        return {"id": new_id, "from": src_id, "bbox": (x0, y0, x1, y1),
                "centre": ((x0 + x1) / 2, (y0 + y1) / 2),
                "cells": len({src.entries[r & ~RING_FLAG].cellid for refs in slots for r in refs})}

    def _entry(self, sidx: int) -> int:
        if sidx not in self.entry_map:
            se = self.src.entries[sidx]
            self.dst.entries.append(BoundaryEntry(list(se.pairs), se.cellid, se.trailer))
            self.entry_map[sidx] = len(self.dst.entries) - 1
        return self.entry_map[sidx]

    def finish(self) -> dict:
        src, dst = self.src, self.dst
        st = {"cells": 0, "new_nodes": 0, "merged_into_existing": 0,
              "entries_dropped": 0, "slot_refs_dropped": 0}
        for cell in sorted(self.cells):
            sni = src.node_for_cell(cell)
            if sni is None:
                continue
            snode = src.nodes.children[sni]
            new0 = []
            for e in _mobs(snode)[0].children:
                lst = list(e[3].value)
                if any(lst[k] not in self.id_map for k in range(0, len(lst), 2)):
                    # An entry is a boundary inside the cell and a multi-id list is the
                    # MERGED boundary of overlapping obstacles (entries of one cell share
                    # their pairs when the obstacles fill it). A merge with a donor
                    # obstacle/character we did not clone has no meaning here; our
                    # obstacle's own single-id entry is complete without it.
                    st["entries_dropped"] += 1
                    continue
                ne = _deep_copy(e)
                _set_int(ne[0], self._entry(e[0].value))
                _set_list(ne[3], [self.id_map[v] if k % 2 == 0 else v for k, v in enumerate(lst)])
                new0.append(ne)
            if not new0:
                continue
            st["cells"] += 1
            dni = dst.node_for_cell(cell)
            if dni is None:
                node = _deep_copy(snode)
                _mobs(node)[0].children = new0
                _mobs(node)[1].children = []
                dst.nodes.children.append(node)
                dst.pairs.append((cell, len(dst.nodes.children) - 1))
                st["new_nodes"] += 1
            else:
                node = dst.nodes.children[dni]
                _mobs(node)[0].children += new0
                st["merged_into_existing"] += 1
            # MOB1 = the set of MOB0's lists (vanilla and URR, every node)
            m0, m1 = _mobs(node)
            have = {tuple(x[1].value) for x in m1.children}
            proto = m1.children[0] if m1.children else _mobs(snode)[1].children[0]
            for ne in new0:
                lst = tuple(ne[3].value)
                if lst not in have:
                    p = _deep_copy(proto)
                    _set_list(p[1], lst)
                    m1.children.append(p)
                    have.add(lst)
        # obstacle slots: only entries that made it into a node
        for item, slots in self.pending:
            ob = _find(item, "OBSTACLE")
            for slot, refs in zip(ob.children[0].children, slots):
                out = []
                for raw in refs:
                    di = self.entry_map.get(raw & ~RING_FLAG)
                    if di is None:
                        st["slot_refs_dropped"] += 1
                    else:
                        out.append(di | (raw & RING_FLAG))
                _set_list(slot[0], out)
        st["manager_added"] = dst.sync_manager()
        dst.flush()
        return st


def donor_obstacles_in(src: ObstacleSystem, inside) -> list[int]:
    """Ids of donor fort obstacles whose bbox centre satisfies inside(x, y)."""
    out = []
    for it in src.forts.children:
        x0, y0, x1, y1 = src.obstacle_bbox(it)
        if inside((x0 + x1) / 2, (y0 + y1) / 2):
            out.append(it[1].value)
    return out


def _node_referenced_ids(self) -> set[int]:
    out = set()
    for node in self.nodes.children:
        out |= self.node_boundary_ids(node)
    return out


ObstacleSystem.node_referenced_ids = _node_referenced_ids


def verify_obstacles(sys_: ObstacleSystem, seq: list[int], cols: int) -> list[str]:
    """Invariants that vanilla satisfies; returns a list of problems."""
    errs = []
    if len(sys_.pairs) != len(sys_.nodes.children):
        errs.append(f"pair count {len(sys_.pairs)} != node count {len(sys_.nodes.children)}")
    seen = set()
    for packed, idx in sys_.pairs:
        if packed in seen:
            errs.append(f"duplicate cell {packed >> 16},{packed & 0xFFFF} in pair list")
        seen.add(packed)
        if not 0 <= idx < len(sys_.nodes.children):
            errs.append(f"pair points at node {idx}, out of range")
            continue
        cell = (packed >> 16) * cols + (packed & 0xFFFF)
        want = seq[cell] if cell < len(seq) else None
        got = sys_.nodes.children[idx][0].value
        if want is not None and got != want:
            errs.append(f"node for cell ({packed >> 16},{packed & 0xFFFF}) has seq {got}, expected {want}")
    missing = [l for l in sys_.node_lists() if l not in sys_.manager_lists()]
    if missing:
        errs.append(f"{len(missing)} boundary list(s) referenced by grid nodes but not "
                    f"registered in OBSTACLE_BOUNDARY_MANAGER: {missing[:4]}")
    if any(l == () for l in sys_.node_lists()):
        errs.append("a grid node references an empty boundary list")
    # cell-level invariants (0 violations on all 7 grids of vanilla and URR)
    node_refs = [0] * len(sys_.entries)
    cross = oob = dup_lists = mob1_bad = 0
    lists_at: dict[int, set] = {}
    for packed, idx in sys_.pairs:
        if not 0 <= idx < len(sys_.nodes.children):
            continue
        m = _mobs(sys_.nodes.children[idx])
        l0 = []
        for e in m[0].children:
            ei = e[0].value
            if not 0 <= ei < len(sys_.entries):
                oob += 1
                continue
            node_refs[ei] += 1
            if sys_.entries[ei].cellid != packed:
                cross += 1
            l0.append(tuple(e[3].value))
            lists_at.setdefault(ei, set()).add(tuple(e[3].value))
        dup_lists += len(l0) != len(set(l0))
        l1 = [tuple(e[1].value) for e in m[1].children] if len(m) > 1 else []
        mob1_bad += set(l1) != set(l0) or len(l1) != len(set(l1))
    if oob:
        errs.append(f"{oob} node entry reference(s) out of range of OBSTACLE_BOUNDARIES")
    if cross:
        errs.append(f"{cross} node entry reference(s) name a boundary entry of ANOTHER cell")
    unref = sum(1 for c in node_refs if c == 0)
    multi = sum(1 for c in node_refs if c > 1)
    if unref or multi:
        errs.append(f"boundary entries listed by no node: {unref}, by several nodes: {multi}")
    if dup_lists:
        errs.append(f"{dup_lists} node(s) list the same id list twice")
    if mob1_bad:
        errs.append(f"{mob1_bad} node(s) whose distinct-list block != set of entry lists")
    for it in sys_.forts.children:
        bid = it[1].value + 1
        for slot in _find(it, "OBSTACLE").children[0].children:
            for raw in slot[0].value:
                ei = raw & ~RING_FLAG
                if ei < len(sys_.entries) and not any(bid in l[0::2] for l in lists_at.get(ei, ())):
                    errs.append(f"obstacle {it[1].value} slot entry {ei} is not listed under its id by the cell's node")
                    break
    n_entries = len(sys_.entries)
    ids = set(sys_.fort_ids_prim.value)
    if len(ids) != len(sys_.forts.children):
        errs.append(f"fort id list {len(ids)} != FORT_OBSTACLE items {len(sys_.forts.children)}")
    for it in sys_.forts.children:
        if it[1].value not in ids:
            errs.append(f"obstacle {it[1].value} missing from the fort id list")
        ob = _find(it, "OBSTACLE")
        for slot in ob.children[0].children:
            for raw in slot[0].value:
                if (raw & ~RING_FLAG) >= n_entries:
                    errs.append(f"obstacle {it[1].value} references boundary entry "
                                f"{raw & ~RING_FLAG} of {n_entries}")
    return errs


def obstacle_copies(osys: ObstacleSystem, grid) -> tuple[dict, set]:
    """Which OBSTACLE_BOUNDARIES entries are plain copies of their cell's pathfinding
    records (deep_dive 10.6): in a cell wholly inside an obstacle's zone every entry is an
    exact copy of the cell's records (a run cell's: one full-cell record), and the node's
    [1] is the cell's record count (9,862 of 9,862). A cell the zone's edge crosses holds
    reshaped entries (type 8 parts, vertices in startpos's own store) and must not change.
    Returns ({cell: [entry index]} for copies, {cells holding a reshaped entry})."""
    from etwpc.io.esf_types import BoundaryEntry
    copies, reshaped = {}, set()
    for packed, ni in osys.pairs:
        rc = (packed >> 16, packed & 0xFFFF)
        k, j = grid.cell_item[rc[0] * grid.cols + rc[1]]
        it = grid.items[k]
        cell = None if (j or not it.bounds) else [x for ab in it.bounds for x in ab]
        for e in _mobs(osys.nodes.children[ni])[0].children:
            ei = e[0].value
            pairs = osys.entries[ei].pairs
            if cell is not None:
                ok = pairs == cell
            else:
                bes = [BoundaryEntry.from_packed(pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2)]
                ok = len(bes) == 1 and bes[0].path_type == 0 and bes[0].path_id == it.pid
            if ok:
                copies.setdefault(rc, []).append(ei)
            else:
                reshaped.add(rc)
    return copies, reshaped


def refresh_obstacle_copies(osys: ObstacleSystem, grid, copies: dict, reshaped: set) -> int:
    """Re-copy the cell records into every copy entry whose cell changed since
    obstacle_copies(), and set each such node's record count. Call flush() after."""
    from etwpc.io.esf_types import BoundaryEntry
    node_of = {(p >> 16, p & 0xFFFF): i for p, i in osys.pairs}
    n = 0
    for rc, eis in copies.items():
        if rc in reshaped:
            continue
        k, j = grid.cell_item[rc[0] * grid.cols + rc[1]]
        it = grid.items[k]
        if j or not it.bounds:
            for ei in eis:
                pairs = list(osys.entries[ei].pairs)
                be = BoundaryEntry.from_packed(pairs[0], pairs[1])
                if be.path_id != it.pid:
                    be.path_id = it.pid
                    pairs[0], pairs[1] = be.to_packed()
                    osys.entries[ei].pairs = pairs
                    n += 1
            nrec = 1
        else:
            cell = [x for ab in it.bounds for x in ab]
            for ei in eis:
                if osys.entries[ei].pairs != cell:
                    osys.entries[ei].pairs = list(cell)
                    n += 1
            nrec = len(it.bounds)
        _set_int(osys.nodes.children[node_of[rc]][1], nrec)
    return n
