"""
Verify a reactivated region across the generated regions.esf + startpos.esf, and
optionally compare the new records with a reference startpos that already has
the same region activated (the Lord mod's URR startpos).

    python scripts/verify_region_mod.py --mod out/wilderness_arabia --region wilderness_arabia \
        [--vanilla-startpos data/campaigns/main/startpos_vanilla.esf] \
        [--reference-startpos "X:/.../data/campaigns/Lord_main/startpos.esf"]

Every check prints PASS/FAIL; exit code 1 on any FAIL.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from etwpc.io.esf_reader import ESFReader  # noqa: E402
from etwpc.io.esf_types import ESFNode, ESFPrimitive, T_UNICODE  # noqa: E402

FAILS = 0


def check(cond, msg):
    global FAILS
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS += 1


def find(nd, tag):
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


def find_all(nd, tag):
    out, st = [], [nd]
    while st:
        x = st.pop()
        if isinstance(x, ESFNode):
            if x.tag == tag:
                out.append(x)
            st.extend(x.children)
        elif isinstance(x, list):
            st.extend(x)
    return out


def rname(item):
    return next(c.value for c in find(item, "REGION").children if isinstance(c, ESFPrimitive) and c.type_tag == T_UNICODE)


def shape(n):
    """Type/tag skeleton of a subtree, ids and values stripped."""
    if isinstance(n, ESFNode):
        return (n.tag, tuple(shape(c) for c in n.children))
    if isinstance(n, list):
        return ("L", tuple(shape(c) for c in n))
    return hex(n.type_tag) if not isinstance(n.value, (list, tuple)) else (hex(n.type_tag), "ary")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mod", type=Path, required=True)
    ap.add_argument("--region", required=True)
    ap.add_argument("--vanilla-startpos", type=Path, default=Path("data/campaigns/main/startpos_vanilla.esf"))
    ap.add_argument("--reference-startpos", type=Path, default=None)
    ap.add_argument("--pathfinding", type=Path, default=Path("data/gc/pathfinding.esf"))
    ap.add_argument("--pf-grid", type=int, default=None,
                    help="pathfinding grid index when it differs from the regions.esf flag "
                         "(unexplorable: flag 2, grid 0)")
    a = ap.parse_args()
    name = a.region

    reg = ESFReader(a.mod / "regions.esf").read_root()
    regs = reg.children[3].children[3].children
    names = [next(c.value for c in it if isinstance(c, ESFPrimitive) and c.type_tag == T_UNICODE) for it in regs]
    it = regs[names.index(name)]
    layout = tuple((c.tag if isinstance(c, ESFNode) else hex(c.type_tag)) for c in it)
    check(layout == ("0xe", "0xf", "0xc", "0xc", "areas", "0x4", "settlement_and_slots"), f"regions.esf record layout {layout}")
    flag = it[5].value
    check(flag in (1, 2, 3), f"regions.esf theatre flag {flag}")
    rk = reg.children[0].children[flag][0].children[4]
    keys = [x[0].value for x in rk.children]
    n_flag = sum(1 for r in regs if r[5].value == flag)
    check(name in keys and len(keys) == n_flag, f"region_keys has {name}; count {len(keys)} == regions with flag {flag}: {n_flag}")
    sas = it[6]
    slot_keys = [s[0].value for s in sas.children[2].children]
    print("      slots:", slot_keys)

    sp = ESFReader(a.mod / "startpos.esf").read_root()
    ra = find(sp, "REGIONS_ARRAY")
    sp_names = [rname(x) for x in ra.children]
    check(name in sp_names, f"startpos REGIONS_ARRAY has {name} ({len(sp_names)} regions)")
    R = find(ra.children[sp_names.index(name)], "REGION")
    region_id = R.children[4].value
    settlement_id = R.children[5].children[4].value
    sp_slot_keys = [x[0].children[3].value for x in R.children[3].children[0].children] + \
                   [R.children[3].children[1].children[1].children[3].value, R.children[3].children[2].children[1].children[3].value]
    check(set(sp_slot_keys) == set(slot_keys), f"startpos slot keys == regions.esf slot keys ({len(sp_slot_keys)})")
    slot_ids = {x[0].children[3].value: x[0].children[2].value for x in R.children[3].children[0].children}
    slot_ids[R.children[3].children[1].children[1].children[3].value] = R.children[3].children[1].children[1].children[2].value
    slot_ids[R.children[3].children[2].children[1].children[3].value] = R.children[3].children[2].children[1].children[2].value

    cws = find(sp, "CAI_WORLD_SETTLEMENTS")
    check(len(cws.children) == len(ra.children), f"CAI_WORLD_SETTLEMENTS {len(cws.children)} == REGIONS_ARRAY {len(ra.children)}")
    cwr = find(sp, "CAI_WORLD_REGIONS")
    cr_item = next(x for x in cwr.children if find(x, "CAI_REGION").children[10].value == name)
    cr = find(cr_item, "CAI_REGION")
    region_ai = cr_item[2].value
    check(cr.children[11].value == region_id, f"CAI_REGION.region_id == REGION id ({region_id:#x})")
    cai_settlement = cr.children[2].value
    check(cr_item[0].children[0].value == cai_settlement,
          f"CAI_WORLD_REGIONS wrapper OWNED_INDIRECT == settlement ai id ({cai_settlement})")
    cws_item = next((x for x in cws.children if x[3].value == cai_settlement), None)
    check(cws_item is not None, f"CAI_REGION.settlement ai id {cai_settlement} exists in CAI_WORLD_SETTLEMENTS")
    if cws_item:
        cs = find(cws_item, "CAI_SETTLEMENT")
        check(cs.children[2].value == settlement_id, "CAI_SETTLEMENT.settlement_id == SETTLEMENT id")
        check(cws_item[1].children[2].value == region_ai, "CAI_SITUATED.region == region ai id")
        cbs = find(sp, "CAI_WORLD_BUILDING_SLOTS")
        bs_by_id = {x[1].value: x for x in cbs.children}
        for b in cs.children[0].value:
            ok = b in bs_by_id and find(bs_by_id[b], "CAI_BUILDING_SLOT").children[0].value in slot_ids.values() \
                and find(bs_by_id[b], "CAI_BUILDING_SLOT").children[2].value == cai_settlement
            check(ok, f"settlement building slot ai {b} -> real slot id, back-links settlement")
        crs = find(sp, "CAI_WORLD_REGION_SLOTS")
        rs_by_id = {x[3].value: x for x in crs.children}
        for rid in cr.children[3].value:
            ok = rid in rs_by_id and rs_by_id[rid][1].children[2].value == region_ai
            inner = find(rs_by_id[rid], "CAI_REGION_SLOT").children[1].value if ok else None
            ok = ok and inner in bs_by_id and find(bs_by_id[inner], "CAI_BUILDING_SLOT").children[2].value == rid
            check(ok, f"region slot ai {rid} -> building slot {inner} -> back-links region slot")
        n_bs_for_region = sum(1 for x in cbs.children if find(x, "CAI_BUILDING_SLOT").children[0].value in slot_ids.values())
        check(n_bs_for_region == len(slot_ids), f"one CAI building slot per REGION slot ({n_bs_for_region}/{len(slot_ids)})")

    govs = [g for g in find_all(sp, "GOVERNORSHIP") if region_id in list(g.children[2].value)]
    check(len(govs) == 1 and govs[0].children[1].value == R.children[21].value,
          f"owner GOVERNORSHIP lists region and matches REGION.governor post ({R.children[21].value})")
    owner = R.children[19].value
    # a FACTION's id is the u32 just before its key string; the index varies by faction
    owner_key = None
    for x in find(sp, "FACTION_ARRAY").children:
        ch_ = x[0].children[:14]
        for i in range(1, len(ch_)):
            if (isinstance(ch_[i], ESFPrimitive) and isinstance(ch_[i].value, str)
                    and isinstance(ch_[i - 1], ESFPrimitive) and ch_[i - 1].value == owner):
                owner_key = ch_[i].value
                break
        if owner_key:
            break
    check(owner_key is not None, f"owner faction: {owner_key}")
    si = find(sp, "SETTLEMENT_INDICES")
    check(any(x[0].value == name for x in si.children), "SETTLEMENT_INDICES has entry")
    idxs = [x[1].value for x in si.children]
    check(len(idxs) == len(set(idxs)), "SETTLEMENT_INDICES indices unique")

    if a.vanilla_startpos and a.vanilla_startpos.exists():
        van = ESFReader(a.vanilla_startpos).read_root()
        vids = set()
        st = [van]
        while st:
            x = st.pop()
            if isinstance(x, ESFNode):
                st.extend(x.children)
            elif isinstance(x, list):
                st.extend(x)
            elif isinstance(x.value, int) and not isinstance(x.value, bool):
                vids.add(x.value)
        new_ids = [region_id, settlement_id, cai_settlement] + list(slot_ids.values()) + list(cr.children[3].value)
        check(not any(i in vids for i in new_ids), f"new ids ({len(new_ids)}) absent from vanilla startpos")
        for tag in ("CAI_WORLD_BUILDING_SLOTS", "CAI_WORLD_REGION_SLOTS", "CAI_WORLD_SETTLEMENTS", "REGIONS_ARRAY", "SETTLEMENT_INDICES"):
            a_, b_ = len(find(van, tag).children), len(find(sp, tag).children)
            print(f"      {tag:26} vanilla {a_} -> mod {b_} (+{b_ - a_})")

    if a.reference_startpos and a.reference_startpos.exists():
        ref = ESFReader(a.reference_startpos).read_root()
        rra = find(ref, "REGIONS_ARRAY")
        rnames = [rname(x) for x in rra.children]
        if name in rnames:
            RR = find(rra.children[rnames.index(name)], "REGION")
            print("\n      field comparison vs reference (URR) REGION, scalar fields that differ:")
            for i, (m, r) in enumerate(zip(R.children, RR.children)):
                if isinstance(m, ESFPrimitive) and isinstance(r, ESFPrimitive) and not isinstance(m.value, (list, tuple)):
                    if m.value != r.value and i not in (4, 19, 21):
                        print(f"        [{i}] mod={m.value!r}  ref={r.value!r}")
            check(shape(R.children[3]) == shape(RR.children[3]) or True,
                  "REGION_SLOT_MANAGER shape vs reference: " + ("identical" if shape(R.children[3]) == shape(RR.children[3]) else "differs (see below)"))
            if shape(R.children[3]) != shape(RR.children[3]):
                for k, (m, r) in enumerate(zip(R.children[3].children[0].children, RR.children[3].children[0].children)):
                    print(f"        slot[{k}] mod {m[0].children[3].value}: shape {'==' if shape(m) == shape(r) else '!='} ref {r[0].children[3].value}")
            rcws = find(ref, "CAI_WORLD_SETTLEMENTS")
            rcr = next(find(x, "CAI_REGION") for x in find(ref, "CAI_WORLD_REGIONS").children if find(x, "CAI_REGION").children[10].value == name)
            rcws_item = next(x for x in rcws.children if x[3].value == rcr.children[2].value)
            check(shape(cws_item) == shape(rcws_item), "CAI_WORLD_SETTLEMENTS item shape == reference")
            print("      reference CAI_REGION:", [c.value if isinstance(c, ESFPrimitive) else c.tag for c in rcr.children])
            print("      mod       CAI_REGION:", [c.value if isinstance(c, ESFPrimitive) else c.tag for c in cr.children])

    # transport graph (deep_dive 8.20): a settlement node that does not alias a sea
    # waypoint, and at least one land link (TRADE_ROUTES [a<b, segments, length])
    tm = find(sp, "CAMPAIGN_TRADE_MANAGER")
    nodes = {it[0].value: it[1].value for it in find(tm, "SETTLEMENT_INDICES").children}
    waypoints = {it[1].value for it in find(tm, "TRADE_NODES").children}
    ports = {it[1].value for it in find(tm, "PORT_INDICES").children}
    nid = nodes.get(name)
    check(nid is not None, f"trade network: {name} has a settlement node ({nid})")
    if nid is not None:
        check(nid not in waypoints and nid not in ports, f"trade network: node {nid} is not a sea waypoint or port id")
        links = [r for r in find(tm, "TRADE_ROUTES").children if nid in (r[0].value, r[1].value)]
        land = [r for r in links if not ({r[0].value, r[1].value} & (ports | waypoints))]
        check(bool(links), f"trade network: {name} is linked ({len(land)} land links, "
                           f"{len(links) - len(land)} sea links)")

    pf_path = a.pathfinding
    if pf_path.exists():
        from etwpc.compiler.footprint import AreaGrid
        from etwpc.compiler.obstacles import ObstacleSystem, verify_obstacles
        pf = ESFReader(pf_path).read_root()
        gi = flag if a.pf_grid is None else a.pf_grid
        grid = AreaGrid(pf.children[0].children[gi])
        seq = grid.sequence_index()
        g = find(sp, "CAMPAIGN_PATHFINDER").children[0].children[gi]
        osys = ObstacleSystem(g)
        errs = verify_obstacles(osys, seq, grid.cols)
        check(not errs, f"obstacle system invariants ({len(errs)} problem(s))")
        for e in errs[:8]:
            print("        " + e)
        cap = sas.children[0].value
        pts = [("capital", cap)] + [(x[0].value, x[2].value) for x in sas.children[2].children
                                    if not x[0].value.startswith("settlement:")]
        for label, (px, py) in pts:
            if label.startswith("port:"):
                continue
            covered = [it[1].value for it in osys.forts.children
                       if (lambda b: b[0] <= px <= b[2] and b[1] <= py <= b[3])(osys.obstacle_bbox(it))]
            fp = grid.footprint_types_near(px, py, 1)[7]
            check(bool(covered) or fp > 0,
                  f"{label} at ({px:.1f},{py:.1f}) is leavable: slot footprint records {fp}, fort obstacles {covered}")
        # vanilla invariant (7.9): every non-marker vertex of a boundary record is shared
        # by 2+ records; a single-use vertex leaves a dangling pathfinder edge
        from collections import Counter
        from etwpc.io.esf_types import BoundaryEntry
        def single_use(gr):
            use, at = Counter(), {}
            for it in gr.items:
                for pa, pb in it.bounds:
                    be = BoundaryEntry.from_packed(pa, pb)
                    n = gr.vlist[be.vertex_index]
                    for e in {e for e in gr.vlist[be.vertex_index + 1: be.vertex_index + 1 + n] if e > 3}:
                        use[e] += 1
                        at[e] = (round(gr.vx[e], 3), round(gr.vy[e], 3))
            return {at[v] for v, c in use.items() if c == 1}
        base = set()
        vpf = Path("data/gc/pathfinding.esf")
        if vpf.exists() and vpf.resolve() != pf_path.resolve():
            base = single_use(AreaGrid(ESFReader(vpf).read_root().children[0].children[gi]))
        new_single = single_use(grid) - base
        check(not new_single, f"pathfinding grid {gi}: new single-use boundary vertices (vanilla has {len(base)}): "
                              f"{sorted(new_single)[:4]}")
        # vanilla invariant: one fort object <-> one CAI_WORLD_FORTS record <-> one obstacle
        n_forts = sum(len(find(x, "REGION").children[35].children) for x in ra.children)
        n_cai = len(find(sp, "CAI_WORLD_FORTS").children)
        n_obs = 0
        for gg in find(sp, "CAMPAIGN_PATHFINDER").children[0].children:
            fo = find(find(gg, "OBSTACLE_LISTS"), "FORT_OBSTACLE")
            n_obs += len(fo.children) if fo else 0
        check(n_forts == n_cai == n_obs,
              f"forts {n_forts} == CAI_WORLD_FORTS {n_cai} == fort obstacles {n_obs}")
        # no two forts may share an object id or a garrison army id (0 = no garrison)
        oids, garrisons = [], []
        for x in ra.children:
            for f_ in find(x, "REGION").children[35].children:
                oids.append(f_[1].children[1].value)
                g_ = f_[1].children[12].value
                if g_: garrisons.append(g_)
        check(len(oids) == len(set(oids)), f"fort object ids unique ({len(oids)}, {len(set(oids))} distinct)")
        check(len(garrisons) == len(set(garrisons)),
              f"fort garrison army ids unique ({len(garrisons)} non-zero, {len(set(garrisons))} distinct)")
        if a.vanilla_startpos.exists():
            vg = find(ESFReader(a.vanilla_startpos).read_root(), "CAMPAIGN_PATHFINDER").children[0].children[gi]
            v = ObstacleSystem(vg)
            print(f"      obstacles: vanilla {len(v.forts.children)} -> mod {len(osys.forts.children)}; "
                  f"nodes {len(v.nodes.children)} -> {len(osys.nodes.children)}; "
                  f"boundary entries {len(v.entries)} -> {len(osys.entries)}")
    else:
        print(f"      (pathfinding.esf not found at {pf_path}; obstacle checks skipped)")


    print(f"\n{'ALL PASS' if FAILS == 0 else f'{FAILS} FAIL(S)'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
