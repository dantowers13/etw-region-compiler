"""
Unlock the minor factions as selectable in startpos.esf.

The Grand Campaign selection screen does NOT read the FACTION records. It reads the
summary block at the top of the file, CAMPAIGN_PREOPEN_MAP_INFO, and a faction is
selectable only if all of these agree (measured 2026-10-03 against the user's DarthMod
startpos, campaign 'DMUC_Umain', which makes Norway and Mughal selectable):

  CAMPAIGN_PREOPEN_MAP_INFO
    PLAYERS_ARRAY[i]  CAMPAIGN_PLAYER_SETUP[4] "Playable"           False -> True
    FACTION_INFOS[i]  (key, portrait, flag, SELECTABLE, n, desc, flag path, regions)
                      [2],[3]: majors True,True; vanilla Mughal True,False (not
                      selectable); DMUC Norway/Mughal True,True; portrait non-empty
    VICTORY_CONDITION_OPTIONS  one (key, VICTORY_CONDITIONS_BLOCK) per selectable
                      faction; DMUC Norway's: four conditions on its own region

The FACTION records get the same treatment as DMUC's (CAMPAIGN_PLAYER_SETUP[4] and the
FACTION-level CAMPAIGN_VICTORY_CONDITIONS record after its True flag), but neither
that nor the DB factions table (DMUC Norway is selectable with the stock table)
changes the list on its own (both tested 2026-10-03).

Run from repo root:
    python scripts/unlock_factions.py [--src STARTPOS] [--dest STARTPOS]
e.g. unlock a region build's startpos and install it with deploy_mod.py:
    python scripts/unlock_factions.py --src out/new_regions/startpos.esf --dest out/new_regions_unlocked/startpos.esf
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from etwpc.io.esf_reader import ESFReader
from etwpc.io.esf_writer import ESFWriter
from etwpc.io.esf_types import ESFNode, ESFPrimitive, T_BOOL, T_BOOL_T, T_BOOL_F
from reactivate_region import find, deep_copy, set_int, set_str, sp_region_name

SRC  = ROOT / "data/campaigns/main/startpos_vanilla.esf"
DEST = ROOT / "data/campaigns/main/startpos_unlocked.esf"

# Not worth offering even though they hold land at the start.
SKIP = {"pirates"}
# Factions whose FACTION_INFOS portrait is empty borrow a related faction's.
PORTRAIT_FROM = {"thirteen_colonies": "britain", "louisiana": "france", "chechenya_dagestan": "georgia",
                 "wallachia": "georgia"}
# DMUC's Norway: (bool, year, turns/regions, bool, condition type), then False, False.
VC_TEMPLATE = [(False, 1799, 25, False, 1), (False, 1799, 25, False, 3),
               (False, 1750, 15, False, 0), (False, 1799, 20, True, 2)]

# ---------------------------------------------------------------------------

def find_child_by_tag(node: ESFNode, tag: str) -> ESFNode | None:
    """Find first direct child ESFNode with the given tag (T_RECORD nodes only)."""
    for c in node.children:
        if isinstance(c, ESFNode) and c.tag == tag:
            return c
    return None


def is_bool(c) -> bool:
    return isinstance(c, ESFPrimitive) and c.type_tag in (T_BOOL, T_BOOL_T, T_BOOL_F)


def set_bool(b: ESFPrimitive, v: bool) -> None:
    """T_BOOL_T/T_BOOL_F are payload-less tag-only booleans; swap tag as well as value."""
    assert is_bool(b), repr(b)
    if b.type_tag != T_BOOL:
        b.type_tag = T_BOOL_T if v else T_BOOL_F
    b.value = v
    b.raw = b""


def faction_regions(root) -> dict[str, list[str]]:
    """Faction key -> regions it owns at the start (REGION[19] = owner faction id)."""
    key_of = {}
    for it in find(root, "FACTION_ARRAY").children:
        f = it[0]
        key = find(f, "CAMPAIGN_PLAYER_SETUP").children[2].value
        for i, c in enumerate(f.children[:14]):
            if isinstance(c, ESFPrimitive) and c.value == key and i > 0:
                key_of[f.children[i - 1].value] = key
    out: dict[str, list[str]] = {}
    for it in find(root, "REGIONS_ARRAY").children:
        owner = key_of.get(find(it, "REGION").children[19].value)
        if owner:
            out.setdefault(owner, []).append(sp_region_name(it))
    return out


def unlock_faction_record(faction: ESFNode, all_factions: bool) -> str | None:
    """FACTION record: Playable flag + FACTION-level victory conditions. Returns the key
    if this faction is to be unlocked."""
    cps = find_child_by_tag(faction, "CAMPAIGN_PLAYER_SETUP")
    # children[4] = Playable bool  (0=victory_conditions, 1=INGAME_MODIFIABLES,
    #                                2=s(name), 3=bool, 4=Playable, 5=bool)
    playable = cps.children[4]
    assert is_bool(playable), f"Unexpected child[4] type: {playable}"
    name = cps.children[2].value

    kids = faction.children
    fum = [i for i, c in enumerate(kids) if isinstance(c, ESFNode) and c.tag == "FORT_UPGRADE_MANAGER"]
    capital = kids[fum[-1] + 1].value
    has_vc = isinstance(kids[fum[0] - 1], ESFNode) and kids[fum[0] - 1].tag == "CAMPAIGN_VICTORY_CONDITIONS"
    if playable.value and has_vc:
        print(f"  already playable: {name}")
        return None
    if name in SKIP or (not capital and not all_factions):
        print(f"  skipped ({'excluded' if name in SKIP else 'no land at start'}): {name}")
        return None
    set_bool(playable, True)
    if not has_vc:
        flag = kids[fum[0] - 1]
        assert is_bool(flag) and not flag.value, f"{name}: expected False VC flag, got {flag!r}"
        set_bool(flag, True)
        kids.insert(fum[0], deep_copy(cps.children[0]))
    return name


def unlock_preopen(root, names: list[str]) -> None:
    """CAMPAIGN_PREOPEN_MAP_INFO: what the selection screen actually reads."""
    pre = find(root, "CAMPAIGN_PREOPEN_MAP_INFO")
    players = {it[0].children[2].value: it[0] for it in find(pre, "PLAYERS_ARRAY").children}
    infos = {it[0].value: it for it in find(pre, "FACTION_INFOS").children}
    vco = find(pre, "VICTORY_CONDITION_OPTIONS")
    have_vc = {it[0].value for it in vco.children}
    owned = faction_regions(root)

    proto = vco.children[0]                                   # (key, VICTORY_CONDITIONS_BLOCK)
    cond_proto = proto[1].children[0]                         # (CAMPAIGN_VICTORY_CONDITIONS,)
    key_proto = cond_proto[0].children[0].children[0]         # (region key,)
    for name in names:
        set_bool(players[name].children[4], True)
        info = infos[name]
        set_bool(info[2], True)
        set_bool(info[3], True)
        if not info[1].value:
            set_str(info[1], infos[PORTRAIT_FROM[name]][1].value)
        if name in have_vc:
            continue
        regions = owned.get(name, [])
        assert regions, f"{name}: owns no region"
        entry = deep_copy(proto)
        set_str(entry[0], name)
        conds = []
        for flag_a, year, n, flag_b, kind in VC_TEMPLATE:
            c = deep_copy(cond_proto)
            vc = c[0]
            rk = []
            for r in regions:
                k = deep_copy(key_proto)
                set_str(k[0], r)
                rk.append(k)
            vc.children[0].children = rk
            set_bool(vc.children[1], flag_a)
            set_int(vc.children[2], year)
            set_int(vc.children[3], n)
            set_bool(vc.children[4], flag_b)
            set_int(vc.children[5], kind)
            set_bool(vc.children[6], False)
            set_bool(vc.children[7], False)
            conds.append(c)
        entry[1].children = conds
        vco.children.append(entry)
    print(f"  preopen: {len(names)} factions selectable, VICTORY_CONDITION_OPTIONS now {len(vco.children)}")


def unlock(root: ESFNode, all_factions: bool = False) -> list[str]:
    """Make every minor that owns land at the start selectable. Returns the keys changed."""
    names = []
    for item in find(root, "FACTION_ARRAY").children:
        faction = item[0]                    # FACTION T_RECORD
        assert isinstance(faction, ESFNode) and faction.tag == "FACTION", repr(faction)
        name = unlock_faction_record(faction, all_factions)
        if name:
            names.append(name)
            print(f"  unlocked: {name}")
    unlock_preopen(root, names)
    return names


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--dest", type=Path, default=DEST)
    ap.add_argument("--all", action="store_true", help="also unlock factions that start with no land")
    a = ap.parse_args()
    a.dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Reading {a.src} ({a.src.stat().st_size:,} bytes)...")
    reader = ESFReader(a.src)
    root = reader.read_root()

    print("Scanning factions...")
    names = unlock(root, all_factions=a.all)
    print(f"\nUnlocked {len(names)} factions.")

    print(f"Writing {a.dest}...")
    data = ESFWriter(root, reader.tag_names, reader.timestamp).to_bytes()
    a.dest.write_bytes(data)
    print(f"Done. {len(data):,} bytes (original: {a.src.stat().st_size:,} bytes)")

    # Round-trip check
    reader2 = ESFReader(a.dest)
    root2 = reader2.read_root()
    data2 = ESFWriter(root2, reader2.tag_names, reader2.timestamp).to_bytes()
    if data == data2:
        print("Round-trip PASS")
    else:
        print("WARNING: round-trip mismatch — do not deploy")


if __name__ == "__main__":
    main()
