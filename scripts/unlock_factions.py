"""
Unlock all factions as playable in startpos.esf.

For each FACTION record, CAMPAIGN_PLAYER_SETUP.children[4] is the
"Playable" boolean (confirmed from taw XML dumps: france=yes, hannover=no).
This script flips every no→yes.

Run from repo root:
    python scripts/unlock_factions.py

Output: data/campaigns/main/startpos_unlocked.esf
Deploy: copy over data/campaigns/main/startpos.esf (backup first)
"""

import sys
import struct
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from etwpc.io.esf_reader import ESFReader
from etwpc.io.esf_writer import ESFWriter
from etwpc.io.esf_types import ESFNode, ESFPrimitive, T_BOOL, T_BOOL_T, T_BOOL_F

SRC  = ROOT / "data/campaigns/main/startpos_vanilla.esf"
DEST = ROOT / "data/campaigns/main/startpos_unlocked.esf"

# ---------------------------------------------------------------------------

def find_child_by_tag(node: ESFNode, tag: str) -> ESFNode | None:
    """Find first direct child ESFNode with the given tag (T_RECORD nodes only)."""
    for c in node.children:
        if isinstance(c, ESFNode) and c.tag == tag:
            return c
    return None

def unlock(root: ESFNode) -> int:
    """Flip Playable boolean in every FACTION. Returns count modified."""
    # root(CAMPAIGN_STARTPOS) → [3]CAMPAIGN_ENV → [5]CAMPAIGN_MODEL
    #   → [4]WORLD → [3]FACTION_ARRAY T_RECORD_ARY
    #   items → each item[0] = FACTION T_RECORD
    campaign_env  = root.children[3]           # CAMPAIGN_ENV
    campaign_model = campaign_env.children[5]  # CAMPAIGN_MODEL
    world          = campaign_model.children[4] # WORLD
    factions_ary   = world.children[3]          # FACTION_ARRAY T_RECORD_ARY
    assert factions_ary.tag == "FACTION_ARRAY", factions_ary.tag

    changed = 0
    for item in factions_ary.children:       # each item is list[ESFNode|ESFPrimitive]
        faction = item[0]                    # FACTION T_RECORD
        assert isinstance(faction, ESFNode) and faction.tag == "FACTION", repr(faction)

        cps = find_child_by_tag(faction, "CAMPAIGN_PLAYER_SETUP")
        if cps is None:
            continue

        # children[4] = Playable bool  (0=victory_conditions, 1=INGAME_MODIFIABLES,
        #                                2=s(name), 3=bool, 4=Playable, 5=bool)
        playable = cps.children[4]
        assert isinstance(playable, ESFPrimitive) and playable.type_tag in (T_BOOL, T_BOOL_T, T_BOOL_F), \
            f"Unexpected child[4] type: {playable}"

        faction_name = cps.children[2].value   # T_ASCII/T_UNICODE string
        if not playable.value:
            # T_BOOL_T/T_BOOL_F are payload-less tag-only booleans; swap tag as well as value
            playable.type_tag = T_BOOL_T if playable.type_tag == T_BOOL_F else playable.type_tag
            playable.value = True
            playable.raw   = b""
            changed += 1
            print(f"  unlocked: {faction_name}")
        else:
            print(f"  already playable: {faction_name}")

    return changed


def main() -> None:
    print(f"Reading {SRC} ({SRC.stat().st_size:,} bytes)...")
    reader = ESFReader(SRC)
    root = reader.read_root()

    print("Scanning factions...")
    n = unlock(root)
    print(f"\nUnlocked {n} factions.")

    print(f"Writing {DEST}...")
    writer = ESFWriter(root, reader.tag_names, reader.timestamp)
    data = writer.to_bytes()
    DEST.write_bytes(data)
    print(f"Done. {len(data):,} bytes (original: {SRC.stat().st_size:,} bytes)")

    # Round-trip check
    reader2 = ESFReader(DEST)
    root2 = reader2.read_root()
    data2 = ESFWriter(root2, reader2.tag_names, reader2.timestamp).to_bytes()
    if data == data2:
        print("Round-trip PASS")
    else:
        print("WARNING: round-trip mismatch — do not deploy")


if __name__ == "__main__":
    main()
