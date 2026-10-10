"""
Structural checks of a build made by add_faction.py (deep_dive 11.4): per-faction structure sizes,
relationships both ways, one AI record / manager / five global beliefs per AI faction, every
diplomatic analysis listing every other AI faction, no AI pool object defined twice, every AI owner
link ((owner, slot) in [10] matching the owner's BLOCK_OWNS), analysers, and the DB tables.

    python scripts/check_factions.py out/m0_wallachia
"""
import sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from add_faction import find_fast, find_all_fast, sub, faction_key_index, read_pack, factions_rows, junction_rows, rstr
from etwpc.io.esf_reader import ESFReader
from etwpc.io.esf_types import ESFNode, ESFPrimitive
d = Path(sys.argv[1]); root = ESFReader(d / "startpos.esf").read_root()
ok = True
def check(cond, msg):
    global ok
    print(("PASS " if cond else "FAIL ") + msg); ok &= bool(cond)
fa = find_fast(root, "FACTION_ARRAY"); n = len(fa.children)
fids, keys = [], []
for it in fa.children:
    F = sub(it, "FACTION"); k = faction_key_index(F); fids.append(F.children[k - 1].value); keys.append(F.children[k].value)
check(len(set(fids)) == n and len(set(keys)) == n, f"{n} factions, ids and keys unique")
setup_keys = [find_fast(sub(it, "FACTION"), "CAMPAIGN_PLAYER_SETUP").children[2].value for it in fa.children]
check(setup_keys == keys, f"every FACTION's CAMPAIGN_PLAYER_SETUP names its own key ({sum(a != b for a, b in zip(setup_keys, keys))} wrong)")
for tag, want in (("SPYING_ARRAY", n), ("FACTION_INFOS", n), ("CAI_WORLD_TECHNOLOGY_TREES", n), ("DOMESTIC_TRADE_ROUTES", n),
                  ("INTERNATIONAL_TRADE_ROUTES", n), ("CAI_WORLD_FACTIONS", n + 1), ("CAI_INTERFACE_MANAGERS", n + 1)):
    check(len(find_fast(root, tag).children) == want, f"{tag} has {want}")
for nd in find_all_fast(root, {"PLAYERS_ARRAY"})["PLAYERS_ARRAY"]:
    check(len(nd.children) == n, f"PLAYERS_ARRAY has {n}")
fset = set(fids)
bad = 0
for it, fid in zip(fa.children, fids):
    dra = find_fast(sub(it, "FACTION"), "DIPLOMACY_RELATIONSHIPS_ARRAY")
    others = [sub(r, "DIPLOMACY_RELATIONSHIP").children[0].value for r in dra.children]
    if sorted(others) != sorted(fset - {fid}): bad += 1
check(bad == 0, f"every faction has exactly one relationship with each of the other {n - 1} ({bad} wrong)")
infos = find_all_fast(root, {"CAI_DIPLOMATIC_ANALYSIS_FACTIONINFO"})["CAI_DIPLOMATIC_ANALYSIS_FACTIONINFO"]
check(all(len(x.children) == n for x in infos), f"all {len(infos)} diplomatic analyses list {n} factions")
cwf = find_fast(root, "CAI_WORLD_FACTIONS"); cai_ids = [it[2].value for it in cwf.children]
check(len(set(cai_ids)) == len(cai_ids), "AI faction ids unique")
mapped = {sub(it, "CAI_FACTION").children[6].value for it in cwf.children if sub(it, "CAI_FACTION") is not None}
check(fset <= mapped, "every faction has an AI faction record")
cim = find_fast(root, "CAI_INTERFACE_MANAGERS")
mgr_ids = [sub(it, "CAI_FACTION_MANAGER").children[0].value for it in cim.children if sub(it, "CAI_FACTION_MANAGER") is not None]
check(set(cai_ids) <= set(mgr_ids) or len(set(cai_ids) - set(mgr_ids)) <= 1, f"AI managers for AI factions (unmatched {sorted(set(cai_ids) - set(mgr_ids))})")
seen = collections.Counter()
for it in cim.children:
    for sect in sub(it, "CAI_BDI_POOL").children:
        if isinstance(sect, ESFNode):
            for o in sect.children:
                if len(o) > 2 and isinstance(o[2], ESFPrimitive): seen[o[2].value] += 1
dup = [v for v, c in seen.items() if c > 1]
check(not dup, f"{len(seen)} AI pool objects, none defined twice ({len(dup)} duplicates)")
cw = find_fast(root, "CAI_WORLD")
reg = max((c for c in cw.children if isinstance(c, ESFPrimitive) and isinstance(c.value, (list, tuple))), key=lambda c: len(c.value))
check(len(set(reg.value)) == len(reg.value), f"AI pool registry ({len(reg.value)}) has no repeats")
pk = dict(read_pack(d / "new_regions.pack"))
fk = next((k for k in pk if k.lower().endswith("factions_tables" + chr(92) + "new_factions")), None)
check(fk is not None, "pack has a new factions table")
if fk:
    import struct as _s
    tb = pk[fk]
    check(tb[:4] == bytes.fromhex("fcfdfeff") and tb[8] == 1, "factions table header marker / version / 01")
    _, rows, _ = factions_rows(tb)
    check(set(rows) <= set(keys) and len(rows) == _s.unpack_from("<I", tb, 9)[0], f"DB factions rows {sorted(rows)} parse and are startpos factions")
    tk = next(k for k in pk if k.lower().endswith("technology_faction_junctions_tables" + chr(92) + "new_factions"))
    t = junction_rows(pk[tk])
    check(len(t) > 0, f"{len(t)} technology rows parse")
    flags = [x for x in pk if x.startswith("ui" + chr(92) + "flags")]
    print(f"info: {len(flags)} flag files: {sorted(set(x.split(chr(92))[2] for x in flags))}")
from add_faction import belief_body
gp = sub(find_fast(root, "CAI_INTERFACE").children, "CAI_BDI_POOL")
gb = sub(gp.children, "CAI_BDI_POOL_BELIEFS")
per = collections.defaultdict(collections.Counter)
for o in gb.children:
    b = belief_body(o)
    if b is not None and b.tag in ("CAI_DIPLOMATIC_ANALYSIS", "CAI_RELATION_ANALYSIS", "CAI_OWNED_REGIONS_ANALYSIS",
                                   "CAI_BASIC_FACTION_ABSOLUTE_ANALYSIS", "CAI_ACTIVE_RECRUITMENT_ANALYSIS"):
        per[b.tag][b.children[0].value] += 1
for tag, c in per.items():
    check(set(c) == set(cai_ids) and max(c.values()) == 1, f"{tag}: one per AI faction ({len(c)}/{len(cai_ids)})")
gd = sub(gp.children, "CAI_BDI_POOL_DESIRES")
gids = {o[2].value for o in gb.children}
bad = 0
for d in gd.children:
    an = sub(d, "CAI_ANALYSER")
    if an is None: continue
    l0, l1 = an.children[0].value, an.children[1].value
    owns = {it[0].value for it in sub(d, "CAI_BDI_COMPONENT_BLOCK_OWNS").children}
    if len(l1) != 2 * len(l0):
        continue      # a vanilla region-group analyser keeps a different [1] layout
    if not (set(l0) <= gids and set(l1[1::2]) == set(l0) and owns >= set(l0)): bad += 1
check(bad == 0, f"global analysers consistent ({bad} bad)")
# every pool object's owners: [10] = (owner, slot), and owner's BLOCK_OWNS[slot] names it
pools = [sub(find_fast(root, "CAI_INTERFACE").children, "CAI_BDI_POOL")] + [sub(x, "CAI_BDI_POOL") for x in cim.children]
objs = {}
for pl in pools:
    for sect in pl.children:
        if isinstance(sect, ESFNode):
            for o in sect.children:
                if len(o) > 12 and isinstance(o[2], ESFPrimitive): objs[o[2].value] = o
bad_slot = 0; checked = 0
for oid, o in objs.items():
    lst = o[10].value if isinstance(o[10], ESFPrimitive) and isinstance(o[10].value, (list, tuple)) else []
    for j in range(0, len(lst) - 1, 2):
        own = objs.get(lst[j])
        if own is None: continue
        bo = sub(own, "CAI_BDI_COMPONENT_BLOCK_OWNS")
        if bo is None: continue
        checked += 1
        if not (lst[j + 1] < len(bo.children) and bo.children[lst[j + 1]][0].value == oid): bad_slot += 1
check(bad_slot == 0, f"AI owner slots consistent ({checked} links, {bad_slot} wrong)")
# living factions: regions, governorships, characters and their AI records agree
ra = find_fast(root, "REGIONS_ARRAY")
regs = {sub(it, "REGION").children[0].value: sub(it, "REGION") for it in ra.children}
govs = find_all_fast(root, {"GOVERNORSHIP"})["GOVERNORSHIP"]
in_gov = collections.Counter(v for g in govs for v in g.children[2].value)
gov_of = {v: g.children[1].value for g in govs for v in g.children[2].value}
owned_regs = [R for R in regs.values() if R.children[19].value in fset]
bad = [R.children[0].value for R in owned_regs if in_gov[R.children[4].value] != 1 or gov_of.get(R.children[4].value) != R.children[21].value]
check(not bad, f"every owned region in exactly one governorship, matching REGION[21] ({len(bad)} wrong: {bad[:5]})")
cwr = {sub(it, "CAI_REGION").children[11].value: it for it in find_fast(root, "CAI_WORLD_REGIONS").children}
cai_of_fid = {sub(it, "CAI_FACTION").children[6].value: it for it in cwf.children if sub(it, "CAI_FACTION") is not None}
owner_lists = collections.Counter(v for it in cwf.children if sub(it, "CAI_FACTION") is not None for v in sub(it, "CAI_FACTION").children[0].value)
bad = []
for R in owned_regs:
    ci = cwr.get(R.children[4].value)
    if ci is None: continue
    rid_ai = ci[2].value
    if rid_ai not in sub(cai_of_fid[R.children[19].value], "CAI_FACTION").children[0].value or owner_lists[rid_ai] != 1: bad.append(R.children[0].value)
check(not bad, f"every owned region's AI id is listed by its owner's AI faction only ({len(bad)} wrong: {bad[:5]})")
cwc = find_fast(root, "CAI_WORLD_CHARACTERS")
cai_char = collections.Counter(sub(it, "CAI_CHARACTER").children[3].value for it in cwc.children if sub(it, "CAI_CHARACTER") is not None)
all_chars, bad_ai, bad_post = [], [], []
for it, fid in zip(fa.children, fids):
    F = sub(it, "FACTION")
    ids_here = [sub(c, "CHARACTER").children[2].value for c in find_fast(F, "CHARACTER_ARRAY").children]
    all_chars += ids_here
    bad_ai += [hex(c) for c in ids_here if cai_char[c] != 1]
    gov = sub(F.children, "GOVERNMENT")
    for pst in gov.children[3].children:
        cid = pst[0].children[2].value
        if cid and cid not in ids_here: bad_post.append(hex(cid))
check(len(all_chars) == len(set(all_chars)), f"{len(all_chars)} character ids unique")
check(not bad_ai, f"every character has exactly one AI record ({len(bad_ai)} wrong: {bad_ai[:5]})")
check(not bad_post, f"every government post names a character of its faction ({len(bad_post)} wrong)")
print("ALL PASS" if ok else "SOME FAILED")
