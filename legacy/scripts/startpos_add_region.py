"""
Add a new region to startpos.esf by splitting an existing parent region.

Edit the CONFIG block at the bottom for each new region split, then run
from the repo root:

    python scripts/startpos_add_region.py

Design notes:
  - Deep-copies the parent REGION record and modifies it surgically.
  - All T_UNICODE / T_ASCII values are renamed via a configurable `string_subs`
    list processed in order (most-specific first to avoid double-replacement).
  - Wealth / population are scaled by a fraction of the parent's values.
  - Object IDs (region ID, building slot IDs, settlement ID) are regenerated
    with CRC32 of the new key strings: deterministic, unique, in 0x30000000 range
    (above vanilla ~0x22-0x24 range).
  - Faction-referencing IDs (GARRISON_RESIDENCE faction ID, controlling_faction_id,
    governor_id) are inherited from the parent so ownership is correct.
  - The fort is removed from the new region (empty FORT_ARRAY) to avoid
    duplicate army IDs.
  - LINE_OF_SIGHT quadtree is copied as-is; the engine rebuilds on first tick.

Confirmed ESF structure (verified by probing vanilla startpos.esf):
  REGIONS_ARRAY T_RECORD_ARY:
    item = [T_RECORD "REGION"]  ← item[0] is REGION directly
  REGION.children:
    [0]  T_UNICODE name
    [1]  POPULATION T_RECORD
    [2]  TRAITS T_RECORD
    [3]  REGION_SLOT_MANAGER T_RECORD
           child[0] = REGION_SLOT_ARRAY T_RECORD_ARY
             each item = [REGION_SLOT T_RECORD]
               REGION_SLOT.children[0] = SIEGEABLE_GARRISON_RESIDENCE
               REGION_SLOT.children[1] = BUILDING_MANAGER
               REGION_SLOT.children[2] = T_U4 building_slot_id
               REGION_SLOT.children[3] = T_UNICODE slot_key
           child[1] = ROAD_SLOT T_RECORD (children[0]=T_BOOL, children[1]=REGION_SLOT)
           child[2] = ROAD_SLOT T_RECORD (walls)
    [4]  T_I4 region_id
    [5]  SETTLEMENT T_RECORD
           child[0] = SIEGEABLE_GARRISON_RESIDENCE
             child[1]  = T_U4 building_slot_id
             child[10] = T_I4 pos_x (fixed-point, div by 1<<20)
             child[11] = T_I4 pos_y (fixed-point, div by 1<<20)
           child[3] = T_UNICODE settlement_key
           child[4] = T_I4 settlement_id
           child[5] = T_UNICODE settlement_key (repeated)
    [6]  T_U4 (1)
    [7]  T_BOOL False
    [8]  T_BOOL False
    [9]  T_U4 wealth_sa
    [10] T_U4 wealth_plus
    [11] T_U4 wealth_plus_minus
    [12] T_U4 town_wealth
    [13] T_U4 town_wealth_min
    [14] T_U4 town_wealth_2
    [15] T_I4 town_monetary_growth
    [16] T_U4 (0)
    [17] T_U4 (0)
    [18] T_BOOL exempt_from_tax
    [19] T_U4 controlling_faction_id  ← preserved from parent
    [20] LINE_OF_SIGHT T_RECORD (children[0]=T_BOOL, [1]=T_V2 min, [2]=T_V2 max, [3]=quadtree)
    [21] T_U4 governor_id             ← preserved from parent
    [22] T_UNICODE theatre
    [23] T_UNICODE emergent_nation
    [24] T_UNICODE rebels_name
    [25] T_UNICODE culture
    [26] REGION_RECRUITMENT_MANAGER T_RECORD
    [27] T_BOOL False
    [28] T_U4_ARY [8 zeros]
    [29] T_U4_ARY [8 zeros]
    [30] T_U4_ARY [8 zeros]
    [31] T_U4_ARY [8 wealth-distribution values]
    [32] T_BOOL_ARY [20 values]
    [33] T_I4_ARY []
    [34] RELIGIOUS_MISSION_BUILDING_ARRAY T_RECORD_ARY
    [35] FORT_ARRAY T_RECORD_ARY       ← cleared to empty
    [36] RESOURCES_ARRAY T_RECORD_ARY  ← each item = [T_UNICODE resource_name]
    [37] T_UNICODE "" (latest construction)
    [38] T_U4 prestige
    [39] T_U4 region_bitmap
    [40] CAMPAIGN_LOCALISATION T_RECORD (children[0] = T_UNICODE display_loc)
    [41] T_F4 float
"""

from __future__ import annotations

import copy
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from etwpc.io.esf_reader import ESFReader
from etwpc.io.esf_writer import ESFWriter
from etwpc.io.esf_types import (
    ESFNode, ESFPrimitive,
    T_ASCII, T_UNICODE,
    T_U4, T_I4, T_V2,
    T_RECORD, T_RECORD_ARY,
)

FIXED_POINT = 1 << 20   # position encoding: int / FIXED_POINT = world units


# ─── Configuration ────────────────────────────────────────────────────────────

@dataclass
class RegionAddConfig:
    """All parameters for one region split operation.

    String substitutions:
        Every T_ASCII / T_UNICODE value in the copied region record is passed
        through `string_subs` in order.  Add pairs most-specific first to
        avoid double-replacement.

    Slot filtering:
        If `slots_to_keep` is None all slots are kept.  Otherwise only slots
        whose key contains at least one entry in `slots_to_keep` are retained.
        Road and wall slots (under REGION_SLOT_MANAGER.children[1:]) are always
        kept and renamed via string_subs.
    """

    # ── Source / target ──────────────────────────────────────────────────────
    parent_region: str
    new_region:    str

    # ── String substitutions (most-specific first) ────────────────────────────
    string_subs: list[tuple[str, str]] = field(default_factory=list)

    # ── Slot filtering ────────────────────────────────────────────────────────
    slots_to_keep: list[str] | None = None

    # ── Named overrides applied after string_subs ─────────────────────────────
    emergent_nation: str | None = None    # e.g. "south_french_rebels"
    rebels_name:     str | None = None    # e.g. "Southern French Rebels"
    display_loc:     str | None = None    # e.g. "regions_onscreen_south_france"
    resources_extra: list[str] = field(default_factory=list)

    # ── Economic scaling (fraction of parent values) ──────────────────────────
    wealth_fraction: float = 0.40
    pop_fraction:    float = 0.40

    # ── Geography ────────────────────────────────────────────────────────────
    capital_wx: float = 0.0
    capital_wy: float = 0.0
    los_min: tuple[float, float] = (-43.75, 291.25)
    los_max: tuple[float, float] = (62.5, 373.75)

    # ── Misc ─────────────────────────────────────────────────────────────────
    prestige: int = 20

    # ── Paths ─────────────────────────────────────────────────────────────────
    input_path:  Path = field(default_factory=lambda: Path("data/campaigns/main/startpos_vanilla.esf"))
    output_path: Path = field(default_factory=lambda: Path("data/campaigns/main/startpos_new_region.esf"))


# ─── ESF utilities ────────────────────────────────────────────────────────────

def deep_copy_esf(node: Any) -> Any:
    if isinstance(node, ESFPrimitive):
        return ESFPrimitive(
            type_tag=node.type_tag,
            value=copy.deepcopy(node.value),
            raw=bytes(node.raw) if node.raw else b"",
        )
    if isinstance(node, ESFNode):
        n = ESFNode(tag=node.tag, type_tag=node.type_tag, version=node.version)
        n.children = [deep_copy_esf(c) for c in node.children]
        return n
    if isinstance(node, list):
        return [deep_copy_esf(c) for c in node]
    return copy.deepcopy(node)


def find_node_by_tag(node: Any, tag: str) -> ESFNode | None:
    if isinstance(node, ESFNode):
        if node.tag == tag:
            return node
        for c in node.children:
            r = find_node_by_tag(c, tag)
            if r:
                return r
    if isinstance(node, list):
        for c in node:
            r = find_node_by_tag(c, tag)
            if r:
                return r
    return None


# ─── ID generation ────────────────────────────────────────────────────────────

def new_id(seed: str) -> int:
    """Deterministic uint32 ID from seed string; lives in 0x30000000-0x3FFFFFFF."""
    return 0x30000000 | (zlib.crc32(seed.encode()) & 0x0FFFFFFF)


# ─── String substitution ──────────────────────────────────────────────────────

def rename_strings_in_subtree(node: Any, subs: list[tuple[str, str]]) -> None:
    """In-place: apply string substitutions to all T_ASCII / T_UNICODE primitives."""
    if isinstance(node, ESFPrimitive):
        if node.type_tag in (T_ASCII, T_UNICODE) and isinstance(node.value, str):
            new_val = node.value
            for old, new in subs:
                new_val = new_val.replace(old, new)
            if new_val != node.value:
                node.value = new_val
                node.raw = None
        return
    children = node.children if isinstance(node, ESFNode) else node
    for c in children:
        rename_strings_in_subtree(c, subs)


# ─── Region record helpers ────────────────────────────────────────────────────

def get_region_record(item: list) -> ESFNode:
    """Return the REGION T_RECORD from a REGIONS_ARRAY item."""
    return item[0]   # item = [T_RECORD "REGION"] — no intermediate wrapper


def get_region_name(item: list) -> str:
    return get_region_record(item).children[0].value


def find_region_item(regions_ary: ESFNode, name: str) -> list:
    for item in regions_ary.children:
        if get_region_name(item) == name:
            return item
    raise KeyError(f"Region '{name}' not found in REGIONS_ARRAY")


# ─── Slot helpers ─────────────────────────────────────────────────────────────

def get_slot_key(slot_item: list) -> str:
    """Return the slot key string from a REGION_SLOT_ARRAY item."""
    return slot_item[0].children[3].value   # item[0] = REGION_SLOT, child[3] = key


def remap_slot_ids(slot_item: list, new_key: str) -> None:
    """Assign fresh building_slot_id to a REGION_SLOT_ARRAY item."""
    region_slot = slot_item[0]                   # REGION_SLOT T_RECORD
    sgr         = region_slot.children[0]        # SIEGEABLE_GARRISON_RESIDENCE
    sid = new_id(f"slot:{new_key}")
    sgr.children[1].value = sid                  # SGR.children[1] = slot_id
    sgr.children[1].raw   = None
    region_slot.children[2].value = sid          # REGION_SLOT.children[2] = slot_id (repeated)
    region_slot.children[2].raw   = None


def remap_road_wall_id(road_slot_node: ESFNode, new_key: str) -> None:
    """Assign a fresh building_slot_id to a ROAD_SLOT / wall node."""
    region_slot = road_slot_node.children[1]     # ROAD_SLOT.children[1] = REGION_SLOT
    sgr         = region_slot.children[0]
    sid = new_id(f"slot:{new_key}")
    sgr.children[1].value = sid
    sgr.children[1].raw   = None
    region_slot.children[2].value = sid
    region_slot.children[2].raw   = None


# ─── Population / wealth helpers ──────────────────────────────────────────────

def scale_population(pop: ESFNode, fraction: float) -> None:
    """Scale population values in a POPULATION T_RECORD."""
    rf = pop.children[0]   # REGION_FACTORS
    for idx in (2, 4):     # current_pop, base_pop in REGION_FACTORS
        rf.children[idx].value = max(1, int(rf.children[idx].value * fraction))
        rf.children[idx].raw   = None
    for idx in (1, 2, 3):  # min_army_pop, base_pop, min_army_pop2 in POPULATION
        pop.children[idx].value = max(1, int(pop.children[idx].value * fraction))
        pop.children[idx].raw   = None


def scale_wealth(region: ESFNode, fraction: float) -> None:
    """Scale all wealth primitives in a REGION T_RECORD."""
    for i in (9, 10, 11, 12, 13, 14):
        p = region.children[i]
        p.value = max(0, int(p.value * fraction))
        p.raw   = None
    # Wealth distribution array (children[31], T_U4_ARY, value is a list)
    wda = region.children[31]
    wda.value = [max(0, int(v * fraction)) for v in wda.value]
    wda.raw   = None


# ─── Settlement helpers ───────────────────────────────────────────────────────

def update_settlement(settlement: ESFNode, new_key: str, wx: float, wy: float) -> None:
    """Update a SETTLEMENT T_RECORD for the new region's capital."""
    sgr = settlement.children[0]   # SIEGEABLE_GARRISON_RESIDENCE
    sid = new_id(f"settlement:{new_key}")

    # Building slot ID
    sgr.children[1].value = sid
    sgr.children[1].raw   = None

    # Position (T_I4 fixed-point pair, NOT T_V2)
    sgr.children[10].value = int(wx * FIXED_POINT)
    sgr.children[10].raw   = None
    sgr.children[11].value = int(wy * FIXED_POINT)
    sgr.children[11].raw   = None

    # Settlement key strings (children[3] and [5]) and settlement ID (children[4])
    for idx in (3, 5):
        settlement.children[idx].value = new_key
        settlement.children[idx].raw   = None
    settlement.children[4].value = sid
    settlement.children[4].raw   = None


# ─── LINE_OF_SIGHT bbox ───────────────────────────────────────────────────────

def update_los_bbox(los: ESFNode, mn: tuple[float, float], mx: tuple[float, float]) -> None:
    """Update the T_V2 bounding box in a LINE_OF_SIGHT T_RECORD."""
    # children[0] = T_BOOL yes/no flag
    # children[1] = T_V2 min, children[2] = T_V2 max
    los.children[1].value = mn
    los.children[1].raw   = None
    los.children[2].value = mx
    los.children[2].raw   = None


# ─── Core: build the new region item ─────────────────────────────────────────

def build_new_region_item(parent_item: list, cfg: RegionAddConfig) -> list:
    """Return a new REGIONS_ARRAY item (deep-copied from parent, modified per cfg)."""

    new_item = deep_copy_esf(parent_item)
    region   = get_region_record(new_item)
    ch       = region.children

    # ── 1. Apply string substitutions throughout the whole record ─────────────
    if cfg.string_subs:
        rename_strings_in_subtree(new_item, cfg.string_subs)
    # Name is now renamed by string_subs but set it explicitly to be safe
    ch[0].value = cfg.new_region
    ch[0].raw   = None
    print(f"  name: '{cfg.parent_region}' -> '{cfg.new_region}'")

    # ── 2. Region ID ──────────────────────────────────────────────────────────
    old_rid = ch[4].value
    ch[4].value = new_id(f"region:{cfg.new_region}")
    ch[4].raw   = None
    print(f"  region_id: 0x{old_rid:08X} -> 0x{ch[4].value:08X}")

    # ── 3. Slot filtering ─────────────────────────────────────────────────────
    rsm      = ch[3]           # REGION_SLOT_MANAGER
    slot_ary = rsm.children[0] # REGION_SLOT_ARRAY T_RECORD_ARY

    if cfg.slots_to_keep is not None:
        # Evaluate against the ORIGINAL keys (before string_subs renamed them)
        # String_subs already ran, so compare against the NEW keys.
        # The config lists parent keys; string_subs has already renamed them.
        # Re-derive new keys from parent keys:
        def to_new_key(k: str) -> str:
            result = k
            for old, new in cfg.string_subs:
                result = result.replace(old, new)
            return result

        new_keys_to_keep = {to_new_key(k) for k in cfg.slots_to_keep}
        before = len(slot_ary.children)
        slot_ary.children = [
            it for it in slot_ary.children
            if get_slot_key(it) in new_keys_to_keep
        ]
        print(f"  slots: {before} -> {len(slot_ary.children)} kept")

    # ── 4. Remap building slot IDs ────────────────────────────────────────────
    for slot_item in slot_ary.children:
        remap_slot_ids(slot_item, get_slot_key(slot_item))
        print(f"    slot: {get_slot_key(slot_item)!r}")

    # Road and wall slots (rsm.children[1], [2])
    for road_slot in rsm.children[1:]:
        if isinstance(road_slot, ESFNode) and len(road_slot.children) >= 2:
            rs = road_slot.children[1]   # REGION_SLOT
            if isinstance(rs, ESFNode) and len(rs.children) > 3:
                remap_road_wall_id(road_slot, rs.children[3].value)

    # ── 5. Settlement ─────────────────────────────────────────────────────────
    settlement   = ch[5]
    new_skey     = settlement.children[3].value   # already renamed by string_subs
    update_settlement(settlement, new_skey, cfg.capital_wx, cfg.capital_wy)
    print(f"  settlement key: {new_skey!r} @ ({cfg.capital_wx}, {cfg.capital_wy})")

    # ── 6. Wealth scaling ─────────────────────────────────────────────────────
    scale_wealth(region, cfg.wealth_fraction)
    print(f"  wealth scaled to {cfg.wealth_fraction:.0%}")

    # ── 7. Population scaling ─────────────────────────────────────────────────
    scale_population(ch[1], cfg.pop_fraction)
    print(f"  population scaled to {cfg.pop_fraction:.0%}")

    # ── 8. Named field overrides ──────────────────────────────────────────────
    if cfg.emergent_nation is not None:
        ch[23].value = cfg.emergent_nation
        ch[23].raw   = None
    if cfg.rebels_name is not None:
        ch[24].value = cfg.rebels_name
        ch[24].raw   = None
    if cfg.display_loc is not None:
        ch[40].children[0].value = cfg.display_loc   # CAMPAIGN_LOCALISATION.children[0]
        ch[40].children[0].raw   = None

    # ── 9. Extra resources ────────────────────────────────────────────────────
    if cfg.resources_extra:
        res_ary = ch[36]   # RESOURCES_ARRAY T_RECORD_ARY
        for res_name in cfg.resources_extra:
            item = [ESFPrimitive(type_tag=T_UNICODE, value=res_name, raw=b"")]
            res_ary.children.append(item)
        print(f"  extra resources: {cfg.resources_extra}")

    # ── 10. Prestige ──────────────────────────────────────────────────────────
    ch[38].value = cfg.prestige
    ch[38].raw   = None

    # ── 11. LINE_OF_SIGHT bbox ────────────────────────────────────────────────
    los = ch[20]
    if los.children[0].value:   # yes flag
        update_los_bbox(los, cfg.los_min, cfg.los_max)
        print(f"  LoS bbox: {cfg.los_min} -> {cfg.los_max}")

    # ── 12. Clear fort (avoids duplicate army IDs) ────────────────────────────
    ch[35].children.clear()   # FORT_ARRAY T_RECORD_ARY
    print(f"  FORT_ARRAY cleared")

    return new_item


# ─── Entry point ──────────────────────────────────────────────────────────────

def add_region(cfg: RegionAddConfig) -> None:
    print(f"Loading {cfg.input_path} ...")
    reader    = ESFReader(cfg.input_path)
    root      = reader.read_root()
    tag_names = reader.tag_names
    timestamp = reader.timestamp

    region_manager = find_node_by_tag(root, 'REGION_MANAGER')
    assert region_manager, "REGION_MANAGER not found"
    regions_ary = find_node_by_tag(region_manager, 'REGIONS_ARRAY')
    assert regions_ary and regions_ary.type_tag == T_RECORD_ARY, \
        "REGIONS_ARRAY T_RECORD_ARY not found"

    print(f"REGIONS_ARRAY: {len(regions_ary.children)} regions")

    parent_item = find_region_item(regions_ary, cfg.parent_region)
    print(f"Found '{cfg.parent_region}'\n")

    print(f"Building '{cfg.new_region}' ...")
    new_item = build_new_region_item(parent_item, cfg)
    regions_ary.children.append(new_item)
    print(f"\nREGIONS_ARRAY: {len(regions_ary.children)} regions ('{cfg.new_region}' appended)")

    print(f"\nWriting {cfg.output_path} ...")
    writer = ESFWriter(root, tag_names, timestamp)
    cfg.output_path.write_bytes(writer.to_bytes())
    out_size = cfg.output_path.stat().st_size
    print(f"Done. {out_size:,} bytes (original: {cfg.input_path.stat().st_size:,} bytes)")

    # Sanity re-read
    print("\nSanity re-read ...")
    reader2   = ESFReader(cfg.output_path)
    root2     = reader2.read_root()
    rm2       = find_node_by_tag(root2, 'REGION_MANAGER')
    ra2       = find_node_by_tag(rm2, 'REGIONS_ARRAY')
    new_found = find_region_item(ra2, cfg.new_region)
    assert get_region_name(new_found) == cfg.new_region
    idx = ra2.children.index(new_found)
    print(f"  '{cfg.new_region}' at index {idx}  PASS")

    # Round-trip check
    original  = cfg.output_path.read_bytes()
    reader3   = ESFReader(cfg.output_path)
    root3     = reader3.read_root()
    rt_bytes  = ESFWriter(root3, reader3.tag_names, reader3.timestamp).to_bytes()
    if rt_bytes == original:
        print(f"  Round-trip PASS ({len(original):,} bytes)")
    else:
        for i, (a, b) in enumerate(zip(original, rt_bytes)):
            if a != b:
                print(f"  Round-trip FAIL at 0x{i:X}: 0x{a:02X} vs 0x{b:02X}")
                break

    print("\nAll done.")


# ─── CONFIG — edit this per region split ──────────────────────────────────────

OCCITANIA_CONFIG = RegionAddConfig(
    parent_region = "france",
    new_region    = "occitania",

    # String substitutions — most-specific slot keys first.
    # The three-step bare rename (protect→rename→restore) prevents double-rename
    # if a placeholder like "___OC___" isn't already present in the parent data.
    string_subs = [
        ("settlement:france:paris:settlement_road",         "settlement:occitania:toulouse:settlement_road"),
        ("settlement:france:paris:settlement_fortification","settlement:occitania:toulouse:settlement_fortification"),
        ("settlement:france:paris:settlement_army-admin",   "settlement:occitania:toulouse:settlement_army-admin"),
        ("settlement:france:paris:settlement_government",   "settlement:occitania:toulouse:settlement_government"),
        ("settlement:france:paris:settlement_culture",      "settlement:occitania:toulouse:settlement_culture"),
        ("settlement:france:paris:settlement_navy-admin",   "settlement:occitania:toulouse:settlement_navy-admin"),
        ("settlement:france:paris:settlement_ordnance",     "settlement:occitania:toulouse:settlement_ordnance"),
        ("settlement:france:paris",                         "settlement:occitania:toulouse"),
        ("wine:france:bordeaux",                            "wine:occitania:bordeaux"),
        ("iron:france:massifcentral",                       "iron:occitania:massifcentral"),
        ("port:france:marseille",                           "port:occitania:marseille"),
        ("town:france:toulouse",                            "town:occitania:toulouse"),
        ("town:france:bordeaux",                            "town:occitania:bordeaux"),
        ("town:france:lyons",                               "town:occitania:lyons"),
        ("town:france:clermont-ferrand",                    "town:occitania:clermont-ferrand"),
        # Bare "france" rename — catches resources_array items, emergent_nation, etc.
        # No protect/restore needed: "occitania" doesn't contain "france".
        ("france",                                          "occitania"),
    ],

    # Keep only slots belonging to southern France / Occitania (PARENT keys).
    slots_to_keep = [
        "settlement:france:paris:settlement_army-admin",
        "settlement:france:paris:settlement_government",
        "settlement:france:paris:settlement_culture",
        "settlement:france:paris:settlement_navy-admin",
        "settlement:france:paris:settlement_ordnance",
        "wine:france:bordeaux",
        "iron:france:massifcentral",
        "port:france:marseille",
        "town:france:toulouse",
        "town:france:bordeaux",
        "town:france:lyons",
        "town:france:clermont-ferrand",
    ],

    emergent_nation = "occitanian_rebels",
    rebels_name     = "Occitanian Rebels",
    display_loc     = "regions_onscreen_occitania",
    resources_extra = ["occitania"],

    wealth_fraction = 0.40,
    pop_fraction    = 0.40,

    # Toulouse world coordinates
    capital_wx = 1.4,
    capital_wy = 311.0,

    los_min = (-34.0, 300.0),
    los_max = ( 53.0, 332.0),

    prestige = 20,

    input_path  = Path("data/campaigns/main/startpos_vanilla.esf"),
    output_path = Path("data/campaigns/main/startpos_occitania.esf"),
)


if __name__ == "__main__":
    add_region(OCCITANIA_CONFIG)
