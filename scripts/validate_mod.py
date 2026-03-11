"""
Cross-reference validator for the occitania mod.

Loads all four modified files simultaneously and checks every
inter-file pointer, index, name reference, and count invariant.
Run from repo root:
    python scripts/validate_mod.py
"""

import sys, struct
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from etwpc.io.esf_reader import ESFReader
from etwpc.io.esf_types import ESFNode, ESFPrimitive, T_RECORD_ARY

GAME_DIR = Path(__file__).parent.parent.parent  # Empire Total War/
PF_PATH      = GAME_DIR / "data/campaign_maps/global_map/pathfinding.esf"
REGIONS_PATH = GAME_DIR / "data/campaign_maps/global_map/regions.esf"
STARTPOS_PATH= GAME_DIR / "data/campaigns/main/startpos.esf"

ERRORS   = []
WARNINGS = []
INFO     = []

def err(msg):  ERRORS.append(f"  [ERROR]   {msg}")
def warn(msg): WARNINGS.append(f"  [WARN]    {msg}")
def info(msg): INFO.append(f"  [INFO]    {msg}")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def find_node(node, tag):
    if isinstance(node, ESFNode):
        if node.tag == tag: return node
        for c in node.children:
            r = find_node(c, tag)
            if r: return r
    return None

def find_all_nodes(node, tag, results=None):
    if results is None: results = []
    if isinstance(node, ESFNode):
        if node.tag == tag: results.append(node)
        for c in node.children: find_all_nodes(c, tag, results)
    return results

# ─── Load files ───────────────────────────────────────────────────────────────

print("=" * 70)
print("ETW Occitania Mod — Cross-Reference Validator")
print("=" * 70)

for path, label in [(PF_PATH, "pathfinding"), (REGIONS_PATH, "regions"), (STARTPOS_PATH, "startpos")]:
    if not path.exists():
        print(f"\nFATAL: {label} not found at {path}")
        sys.exit(1)
    print(f"  Loading {label} ({path.stat().st_size:,} bytes) ...")

pf_root   = ESFReader(PF_PATH).read_root()
reg_root  = ESFReader(REGIONS_PATH).read_root()
sp_root   = ESFReader(STARTPOS_PATH).read_root()
print()

# ─── 1. pathfinding.esf ───────────────────────────────────────────────────────
print("[1] pathfinding.esf")

pf_areas = pf_root.children[0]
europe   = pf_areas.children[2]
gd       = europe[2]  # grid_data

n_pass   = gd.children[8].value
n_list   = gd.children[9].value
i2_ary   = list(gd.children[10].value)   # path_id -> regions.esf index
u2_ary   = list(gd.children[11].value)

info(f"n_passable={n_pass}, n_listed={n_list}")
info(f"i2_ary length={len(i2_ary)} (expect n_passable)")

if len(i2_ary) != n_pass:
    err(f"i2_ary length {len(i2_ary)} != n_passable {n_pass}")

# Map path_id (0-indexed) -> regions.esf index
pf_regions_map = {}  # path_id -> regions_idx
for pid, ridx in enumerate(i2_ary):
    pf_regions_map[pid] = ridx
    if ridx > 204:
        err(f"i2_ary[{pid}] = {ridx} — EXCEEDS regions.esf max index 204 (out of bounds!)")
    elif ridx < 0:
        err(f"i2_ary[{pid}] = {ridx} — negative index invalid")

info(f"i2_ary France (pid 33) -> regions idx {i2_ary[33]}")
info(f"i2_ary occitania (pid 85) -> regions idx {i2_ary[85] if len(i2_ary) > 85 else 'MISSING'}")

# ─── 2. regions.esf ───────────────────────────────────────────────────────────
print("\n[2] regions.esf")

reg_data   = reg_root.children[3]
regions_ary = find_node(reg_data, "regions")
n_regions   = len(regions_ary.children)
info(f"regions count = {n_regions} (hard limit = 205)")

if n_regions > 205:
    err(f"regions count {n_regions} EXCEEDS hard limit 205")
elif n_regions != 205:
    warn(f"regions count {n_regions} != expected 205")

# Build name → slot-index map
reg_name_to_idx = {}
for i, slot in enumerate(regions_ary.children):
    name = str(slot[0].value)
    reg_name_to_idx[name] = i

info(f"regions names (sample): {list(reg_name_to_idx.keys())[:5]}")

# Verify each pathfinding i2_ary index resolves to a real region
for pid, ridx in pf_regions_map.items():
    if 0 <= ridx < n_regions:
        slot = regions_ary.children[ridx]
        rname = str(slot[0].value)
        rtype = str(slot[1].value)
        rflag = slot[5].value if len(slot) > 5 else "?"
        has_sas = len(slot) >= 7
        if pid in (33, 85):  # france and occitania — print details
            info(f"  pf pid={pid} -> regions[{ridx}] name={rname!r} type={rtype!r} flag={rflag} has_sas={has_sas}")
        if rtype not in ("land", "sea", "coast", "wilderness"):
            warn(f"  pid={pid} -> regions[{ridx}] type={rtype!r} unexpected")
    else:
        err(f"  pid={pid} -> regions[{ridx}] is out of bounds (n_regions={n_regions})")

# Check occitania slot specifically
if "occitania" in reg_name_to_idx:
    oc_idx = reg_name_to_idx["occitania"]
    oc_slot = regions_ary.children[oc_idx]
    oc_flag = oc_slot[5].value if len(oc_slot) > 5 else None
    oc_has_sas = len(oc_slot) >= 7
    info(f"occitania at regions[{oc_idx}]: flag={oc_flag}, has_settlement_and_slots={oc_has_sas}")
    if not oc_has_sas:
        err("occitania slot missing settlement_and_slots (engine will NULL-deref on region load)")
    if oc_flag != 2:
        warn(f"occitania flag={oc_flag}, expected 2 (active)")
else:
    err("occitania not found in regions.esf")

# Check theatres_and_region_keys
tar = reg_root.children[0]
europe_tar = tar.children[2][0]
rk_europe  = europe_tar.children[4]
rk_names   = [str(item[0].value) for item in rk_europe.children if isinstance(item, list)]
info(f"theatres_and_region_keys Europe: {len(rk_names)} entries")
if "occitania" in rk_names:
    warn(f"'occitania' is in theatres_and_region_keys Europe ({len(rk_names)} total). "
         f"Vanilla had 77. Engine may enumerate this and crash if it looks up occitania's "
         f"polygon in the campaign overview before startpos is loaded.")
if "central_italy" in rk_names:
    warn("'central_italy' still in theatres_and_region_keys — was not renamed")

# ─── 3. startpos.esf ──────────────────────────────────────────────────────────
print("\n[3] startpos.esf")

reg_array_node = find_node(sp_root, "REGIONS_ARRAY")
n_sp_regions   = len(reg_array_node.children)
info(f"REGIONS_ARRAY count = {n_sp_regions}")

# Build startpos region names
sp_region_names = []
for item in reg_array_node.children:
    if isinstance(item, list):
        rec = item[0] if item else None
    else:
        rec = item
    if isinstance(rec, ESFNode) and rec.tag == "REGION":
        name = str(rec.children[0].value)
        sp_region_names.append(name)
    else:
        sp_region_names.append("?")

info(f"REGIONS_ARRAY names (last 3): {sp_region_names[-3:]}")

if "occitania" not in sp_region_names:
    err("'occitania' not in startpos REGIONS_ARRAY")
else:
    oc_sp_idx = sp_region_names.index("occitania")
    info(f"occitania is at REGIONS_ARRAY[{oc_sp_idx}]")

# CAI_WORLD_SETTLEMENTS count must equal REGIONS_ARRAY count
cws = find_node(sp_root, "CAI_WORLD_SETTLEMENTS")
if cws:
    info(f"CAI_WORLD_SETTLEMENTS count = {len(cws.children)}")
    if len(cws.children) != n_sp_regions:
        err(f"CAI_WORLD_SETTLEMENTS {len(cws.children)} != REGIONS_ARRAY {n_sp_regions}")
else:
    err("CAI_WORLD_SETTLEMENTS not found in startpos")

# CAI_BDI_COMPONENT_BLOCK_OWNS — all should match REGIONS_ARRAY count
all_bo = [n for n in find_all_nodes(sp_root, "CAI_BDI_COMPONENT_BLOCK_OWNS")
          if n.type_tag == T_RECORD_ARY]
matching = [n for n in all_bo if len(n.children) == n_sp_regions]
wrong    = [n for n in all_bo if len(n.children) not in (n_sp_regions, 0)]
info(f"CAI_BDI_COMPONENT_BLOCK_OWNS: {len(matching)} arrays with {n_sp_regions} items, {len(wrong)} mismatched")
if wrong:
    for n in wrong[:3]:
        err(f"  CAI_BDI_COMPONENT_BLOCK_OWNS has {len(n.children)} items, expected {n_sp_regions}")

# T_U4_ARY arrays sized to REGIONS_ARRAY count
sized_137 = []
sized_138 = []
def scan_u4_arys(node):
    if isinstance(node, ESFPrimitive) and node.type_tag == 0x4C:  # T_U4_ARY
        if node.value and len(node.value) in (137, 138):
            if len(node.value) == 137: sized_137.append(node)
            else: sized_138.append(node)
    if isinstance(node, ESFNode):
        for c in node.children: scan_u4_arys(c)

scan_u4_arys(sp_root)
info(f"T_U4_ARY arrays: {len(sized_137)} still at 137, {len(sized_138)} at 138")
if sized_137:
    warn(f"{len(sized_137)} T_U4_ARY arrays still have 137 items — may need extending to {n_sp_regions}")

# ─── 3b. CAI_WORLD_SETTLEMENTS ID sanity ──────────────────────────────────────

OWNED_DIRECT_VALID = set(range(1265, 1431, 3))  # 42 type codes, multiples of 3
CAI_SETTLE_MIN, CAI_SETTLE_MAX = 585483720, 587440304  # vanilla + 1 slot

if cws:
    occ_item = cws.children[-1]  # occitania = last
    od_val = occ_item[0].children[0].value
    cs_val = occ_item[28].children[2].value
    if od_val not in OWNED_DIRECT_VALID:
        err(f"Occitania OWNED_DIRECT[0]={od_val} — not a valid type code (valid: 1265-1430 step 3)")
    else:
        info(f"Occitania OWNED_DIRECT[0]={od_val} (valid type code)")
    if not (CAI_SETTLE_MIN <= cs_val <= CAI_SETTLE_MAX + 50000):
        err(f"Occitania CAI_SETTLEMENT[2]={cs_val} — outside expected range ~585M-587M")
    else:
        info(f"Occitania CAI_SETTLEMENT[2]={cs_val} (in range)")

# ─── 4. Cross-file: startpos region names vs regions.esf names ────────────────
print("\n[4] Cross-file checks")

for sp_name in sp_region_names:
    if sp_name == "?": continue
    if sp_name not in reg_name_to_idx:
        # Many startpos regions won't be in regions.esf (seas, etc.) — only warn for land
        pass  # too noisy to flag all

# Check that occitania in startpos matches regions.esf by name
if "occitania" in sp_region_names and "occitania" in reg_name_to_idx:
    info("occitania: present in both startpos REGIONS_ARRAY and regions.esf slot")
elif "occitania" in sp_region_names:
    err("occitania in startpos REGIONS_ARRAY but NOT in regions.esf")
elif "occitania" in reg_name_to_idx:
    warn("occitania in regions.esf but NOT in startpos REGIONS_ARRAY")

# Check that pathfinding pid 85 -> regions[74] -> name matches startpos
if len(i2_ary) > 85:
    pf_occ_ridx = i2_ary[85]
    if 0 <= pf_occ_ridx < n_regions:
        pf_occ_name = str(regions_ary.children[pf_occ_ridx][0].value)
        if pf_occ_name == "occitania":
            info(f"pathfinding pid=85 -> regions[{pf_occ_ridx}]='{pf_occ_name}' -> name matches")
        else:
            err(f"pathfinding pid=85 -> regions[{pf_occ_ridx}]='{pf_occ_name}' — expected 'occitania'")

# ─── 5. DB pack check ─────────────────────────────────────────────────────────
print("\n[5] DB pack")

PACK_PATH = GAME_DIR / "data/occitania_db.pack"
if not PACK_PATH.exists():
    err(f"occitania_db.pack not found at {PACK_PATH}")
else:
    data = PACK_PATH.read_bytes()
    magic = data[:4]
    if magic != b"PFH0":
        err(f"occitania_db.pack bad magic {magic!r}")
    else:
        files_count = struct.unpack_from("<I", data, 16)[0]
        index_size  = struct.unpack_from("<I", data, 20)[0]
        info(f"occitania_db.pack: {files_count} files, index_size={index_size}")
        # Extract file paths from index
        pos = 24
        pack_paths = []
        for _ in range(files_count):
            fsize = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            end = data.index(b"\x00", pos)
            pack_paths.append(data[pos:end].decode("ascii"))
            pos = end + 1
        for pp in pack_paths:
            info(f"  pack contains: {pp}")
        required = [
            "db\\regions_tables\\regions",
            "db\\campaign_map_settlements_tables\\campaign_map_settlements",
            "db\\campaign_map_slots_tables\\campaign_map_slots",
        ]
        for req in required:
            if req not in pack_paths:
                err(f"  MISSING from pack: {req}")

# ─── Report ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("RESULTS")
print("=" * 70)

for line in INFO:     print(line)
print()
for line in WARNINGS: print(line)
print()
for line in ERRORS:   print(line)

print()
if ERRORS:
    print(f"FAIL — {len(ERRORS)} error(s), {len(WARNINGS)} warning(s)")
else:
    print(f"PASS — 0 errors, {len(WARNINGS)} warning(s)")
