# Deep dive: why the occitania pipeline is shallow, and what "deeper" means

**Date:** 2026-09-06
**Evidence:** `python scripts/inventory_region_refs.py --region france` against vanilla
`pathfinding.esf`, `regions.esf`, `startpos_vanilla.esf` (Grand Campaign). Every number
below comes from that run or from the esf2xml dumps in the game-dir clone's `out/`.

---

## 1. Verdict in one paragraph

The scripts in `scripts/` patch the three campaign files at the *shallowest* layer that
each file exposes: an interior-cell tag in pathfinding, a renamed dormant slot with a
rectangle in regions, and a cloned REGION record plus a few CAI arrays in startpos.
Each file also carries a **derived geometric/graph layer** (pathfinding boundary records
and border groups, the regions mesh + quadtree spatial index, the AI's region graph and
its own copy of the pathfinding grids) that the scripts never touch, so the three files
disagree with each other about where occitania is and who its neighbours are. The
crashes (v1-v8) are the engine noticing those disagreements. The fix is not another
patch script; it is to treat those derived layers as *compiler output* and regenerate
them from one source of truth.

---

## 2. What a region is to the engine (the full dependency graph)

| Layer | File | Structure | Touched by scripts? |
|---|---|---|---|
| Identity / colour | `db/regions_tables` | name, continent, RGB | yes (`build_occitania_db.py`) |
| Overlay raster | `europe_lookup.tga` (490x300, 8-bit paletted; palette RGB == DB RGB) | pixel -> palette -> region | yes, by horizontal row (`update_tga.py`) |
| Region record | `regions.esf` `region_data/regions[205]` | name, type, bbox, theatre flag, areas, settlement_and_slots | yes (slot 74 renamed) |
| **Mesh** | `regions.esf` `region_data/vertices` (67,123 shared v2) + per-area `faces` (triangles) + `outlines` (closed rings) + `connectivity` | polygon per area | partially: rectangle from 4 appended vertices |
| **Spatial index** | `regions.esf` `query_info` quadtree: 3,685 quads, 2,764 cells, 51,534 line segments each tagged `(region, area)` on both sides | "which region is point P in" | **no** |
| Theatre keys | `regions.esf` `theatres_and_region_keys[2].region_keys` (77 entries == 77 regions with flag 2) | GC overview labels | no (deliberately skipped after a crash) |
| Grid cells | `pathfinding.esf` `grid_data[2]` 310x190, 18,175 items | per-cell 8-byte trait + boundary records; 3,409 items carry a `T_U2` region id for a run of interior cells | interior runs only |
| **Boundary records** | 42,379 packed `(passable_part, unknown2, path_type, path_id, vertex_index)` records pointing into a 339,394-entry vertex-index list over 91,375 vertices | region edges, coasts, transition zones | **no** |
| Border groups | `grid_data[2].u2_ary` tail: 183 groups (143 pairs, 39 triples) of 1-based path ids | which regions share a border / junction | mis-parsed (see 4.1) |
| Region -> regions.esf | `grid_data[2].i2_ary[85]` | path_id -> regions index | yes |
| Campaign region | `startpos.esf` `REGION_MANAGER/REGIONS_ARRAY[137]` | economy, slots, settlement, owner | yes (clone of France) |
| **AI world model** | `CAI_WORLD_REGIONS[205]` (indexed like regions.esf, one `CAI_REGION` each with theatre ids, one HLCI per mesh area, slot ids, boundary ids, name, region id) | AI's map | **no** |
| **AI adjacency** | `CAI_WORLD_REGION_BOUNDARIES[446]` (`region A, region B, distance`) and `CAI_WORLD_REGION_HLCIS[1006]` | AI routing / border patrol | **no** |
| AI settlement | `CAI_WORLD_SETTLEMENTS[137]` + 15x `CAI_BDI_COMPONENT_BLOCK_OWNS[137]` + `CAI_ANALYSER` u4 arrays | per-settlement AI state | yes, by "every array of length 137 gets an element" |
| **AI pathfinder copy** | `CAMPAIGN_PATHFINDER/PATHFINDING_GRID[7]` inside startpos (grid[2] has 8,814 cached grid paths) | runtime pathfinder state | **no** |
| Fog / LoS | 2,050 `QUAD_TREE_BIT_ARRAY` + per-region `LINE_OF_SIGHT` | visibility | bbox copied from France |

Bold rows are the derived layers. They are all generated from the same source geometry
by CA's build tools; the game never edits them, it only reads them.

---

## 3. Evidence that the shallow layer is not enough

### 3.1 pathfinding: the split moved 253 cells and missed ~400

France has 78 items whose interior run is tagged `T_U2 == 33` (253 cells in total) and
**397 items** whose boundary records are tagged with France's path id. Items without a
trailing run have no `T_U2` at all; their region membership lives in the boundary
records (`path_type 0`, `path_id 33`) and in the 8-byte trait. `france_split.py` only
rewrites `T_U2`, and only in rows below 96, so:

- every coastal, border and split-line cell of "occitania" still says France,
- no boundary record exists along the new France/occitania border, so the pathfinder
  has no edge to cross and no `passable_part` cost for it,
- the split is a horizontal line at wy = 332, not the shape of any region.

### 3.2 pathfinding: the adjacency section was misread

The 586 values after the 85 sorted path ids are **183 border groups** (143 pairs, 39
triples), e.g. `(19,34) spain-france`, `(34,37) france-savoy`, `(34,50) france-alsace`,
`(34,57) france-flanders`. `rebuild_u2_ary()` assumes entry `i` is the neighbour list of
`sorted_pids[i-1]`, appends 86 to whatever pair sits at index 34 (turning it into a bogus
triple), then appends a duplicate `(34,19)` pair. The neighbour list for occitania is
therefore wrong and France's is corrupted.

### 3.3 regions.esf: the "vertices are exclusive" premise is false

Outline vertex ownership across all 205 regions: 7,751 vertices used by one region,
**36,787 by two**, 646 by three, 4 by four. France shares 97 outline vertices with
bay_of_biscay, 48 with spain, 24 with alsace, 21 with flanders, 19 with savoy. Borders
are shared polylines. The crash that led to the "exclusive vertices" workaround was not
caused by sharing; the most likely cause is the quadtree (3.4) still assigning every
southern-France point to France while a second polygon claimed the same area.

### 3.4 regions.esf: the quadtree is the real spatial index

`query_info` holds 2,764 leaf cells, each with a default `(region, area)` and a list of
line segments `(v1, v2, (region,area) left, (region,area) right)` (51,534 segments).
This is how the engine answers "which region contains this point" without scanning 205
polygons. Nothing in the pipeline adds occitania's edges here, so the engine's answer for
Toulouse remains "france" regardless of what regions[74] says. This alone explains why
settlement placement, region ownership and the GC overview behave inconsistently.

### 3.5 startpos: the AI has its own region graph, untouched

`CAI_WORLD_REGIONS` has 205 entries, indexed exactly like `regions.esf`. France's
`CAI_REGION` carries 12 HLCI ids (one per mesh area), 17 region-slot ids, 8 boundary
ids (its edges in `CAI_WORLD_REGION_BOUNDARIES`) and the startpos region id. Occupying
slot 74 (`central_italy`) means the AI's entry 74 still describes a dormant Italian region
with no boundaries, no HLCIs and no settlement, while `CAI_WORLD_SETTLEMENTS[137]` now
claims a settlement in it. The "hard limit of 205" is not an engine limit; it is this
array (and `CAI_WORLD_RESOURCE_MOBILES`, `CAI_BDI_COMPONENT_BLOCK_OWNS`, the
`CAI_ANALYSER` arrays) not being extended.

### 3.6 startpos: heuristic patching by array length

`fix_startpos_cai2.py` appends a zero to *every* `T_U4_ARY` with exactly 137 elements.
The inventory shows 16 `CAI_ANALYSER`, 16 `CAI_BDI_POOL_DESIRES`, 4
`NAME_ALLOCATION_DETAILS` and 2 `CAI_WORLD_RESOURCE_MOBILES` arrays at that length, with
different meanings (some are id lists, some are per-faction counters, one is a name
allocator). Length is not identity. The same pattern produced the v7/v8 fixes where
CRC32 "ids" had been written into what turned out to be type codes.

### 3.7 startpos: a second copy of the pathfinding grids

`CAMPAIGN_PATHFINDER` holds seven `PATHFINDING_GRID` records (25 MB of XML for grid 2
alone, with 8,814 cached `grid_paths`). Whether the engine trusts this copy or rebuilds it
from `pathfinding.esf` on campaign start is unknown and must be tested before any grid
edit can be called "deployed".

### 3.8 deployment drift

The game currently runs vanilla `pathfinding.esf`/`regions.esf` (identical to `.bak`)
and a 53 MB 2009-era `startpos.esf`, not the 68 MB `startpos.esf.vanilla_backup` every
script was written against. DME (Darthmod launcher) is installed. Any region mod has to be
built on whichever startpos is actually played.

---

## 4. Concrete bugs to fix regardless of strategy

1. `scripts/france_split.py::rebuild_u2_ary` - wrong model of the adjacency section (3.2).
2. `scripts/regions_repurpose_slot74.py` comment block - "vertices are exclusive" (3.3).
3. `scripts/fix_startpos_cai2.py` - length-based array patching (3.6).
4. `scripts/validate_mod.py` - checks counts only; add quadtree membership and
   boundary-record checks once the decoders below exist.
5. `docs/reverse_eng/binary_format.md` and `coordinate_system.md` still say TBD for
   things Phase 0 resolved; `phase0_findings.md` says west_pommerania is present in
   pathfinding while the Jira log says it is merged into Brandenburg. Reconcile.

---

## 5. What "deeper" means: regenerate, do not patch

### 5.1 Single source of truth

One raster: a repainted `europe_lookup.tga` (or a higher-resolution copy of it) where
every pixel holds a region colour. Everything else is derived:

```
region raster (490x300 or finer)
   |-- contour trace  -> per-region rings in world coords (shared border polylines)
   |       |-- simplify (Douglas-Peucker, tolerance ~0.5 wu) -> outlines
   |       |-- constrained triangulation           -> faces
   |       |-- shared vertex table                 -> region_data/vertices
   |       `-- quadtree over segments              -> query_info
   |-- rasterise to 2.0 wu grid -> grid cell region ids
   |       |-- edge tracing  -> boundary records + vertex-index list + pathfinding vertices
   |       `-- pair/triple junction detection -> u2_ary border groups
   `-- adjacency graph -> CAI_WORLD_REGION_BOUNDARIES, CAI_REGION.boundary_ids,
                          HLCIs (one per area), startpos REGION records
```

### 5.2 De-risking experiments (cheap, each answers one question)

Run these before writing generators. Each is a one-file edit, a game launch, and a
yes/no. Record results in this document.

| # | Question | Experiment |
|---|---|---|
| E1 | Does the engine rebuild `query_info` at load? | Delete all segments from one quadtree leaf inside France; click there in game. If region detection breaks, the quadtree is authoritative. |
| E2 | Does the engine rebuild `CAMPAIGN_PATHFINDER` from `pathfinding.esf`? | Empty `grid_paths` for grid 2 in startpos; move an army. |
| E3 | Are boundary records required for movement? (ETWPC-8, never run) | Remove all boundary records from a few interior France items; move an army across them. |
| E4 | Is `CAI_WORLD_REGIONS` mandatory per region? | Append a 206th regions.esf entry *and* a 206th `CAI_WORLD_REGIONS` item (copy of a dormant one). If it loads, the "205 limit" is dead. |
| E5 | What do trait bytes mean? | Compare the 8-byte trait of cells at known distances from coast/border; hypothesis: four 5-bit directional distances (`0x1f` = far, `0xff` = none). |
| E6 | Which startpos is played? | Confirm with DME off and on; diff region counts. |

### 5.3 Build order (replaces the stalled ETWPC-13..19 stubs)

1. **Decoders first** (`src/etwpc/io/`): typed models for `grid_cells` items (trait,
   boundaries, run), `region_data` areas/outlines/faces, `query_info` leaf cells and
   segments, `CAI_WORLD_REGIONS`/`BOUNDARIES`/`HLCIS`. Each with a `to_esf()` that
   reproduces the vanilla bytes (extend the round-trip gate to these models).
2. **Analyser**: given a region name, extract its polygon from the mesh, its cell set from
   the grid, its quadtree segments, its AI record, and assert they agree. This is the
   validator the project has been missing; run it on vanilla to calibrate.
3. **Generators**, in dependency order: mesh (outline + faces + shared vertices) ->
   quadtree -> grid cells and boundary records -> border groups -> AI graph ->
   startpos REGION. Each generator is checked by running the analyser on its output.
4. **Splitter CLI**: takes a parent region and a polygon (from GeoJSON or a painted
   raster), runs the generators, emits the three files plus DB pack, runs the analyser.
5. Only then: occitania, then the Tier A list (Jutland, Zealand, Pommerania, Alsace,
   Lorraine).

### 5.4 The alternative if E1-E4 come back "engine rebuilds it"

If the engine regenerates the quadtree, AI graph and pathfinder copy at load, the deep
compiler collapses to: mesh + grid + boundary records. That is still more than the
current scripts do, but far less than 5.3. The experiments decide the scope; do not
guess.

---

## 6. Open questions carried forward

- `path_id` in boundary records takes 265 distinct values under `path_type 0` although
  only 85 regions exist; values above 84 (e.g. 123, 126, 127 around France) must index
  something else - possibly the border-polyline table rather than a region.
- Semantics of `path_type` 3-7 and of `unknown2` (20 bits, looks like packed flags).
- The 4-byte word after each run cell (`02 00 00 00` for sea, `01 01 11 8f` on land).
- `areas[i][8]` (65535 for passable, 189-193 for the small impassable areas) and
  `areas[i][9]` (104 everywhere in Europe).
- Whether `region_keys` really must stay at 77 or whether the crash on the 78th entry
  was the quadtree disagreement again.

---

## 7. Update 2026-09-06 (later): crash log, the URR precedent, and the first build

### 7.1 The crashes were recorded all along

The Windows Application log holds 70 Empire.exe faults, 44 between 7 and 14 March
2026, at ten distinct code offsets. All ten are null-pointer reads in small accessor
functions (disassembled from Empire.exe 1.5, base 0x400000):

| Offset | Count / when | What it is |
|---|---|---|
| 0x000BFD80 | 6, Mar 8 evening | `mov eax,[ecx]; mov eax,[eax]` with ecx null: the "206th regions entry" runs, i.e. a lookup into an array (CAI_WORLD_REGIONS is 205 long) returned nothing |
| 0x00553086 | 7, Mar 8-11, **and 2 on 2025-03-01 before any modding** | `[obj+0x1dc]->[+0x34]`, sub-object null |
| 0x0056917E | 14, Mar 11 | virtual call chain `->[+0xf8]->[+0x18c]`, null; same object family as above |
| 0x008EA2B3 | 3, Mar 11 | destructor, `[this+0x8c]` object has no vtable |
| 0x004E2165 | 6 | byte reads at `[edx+0xc..0xe]`, edx null |
| 0x00C98D72 | 4, Mar 14 | `WS_LOADING_SCREEN_IMP::pf_on_create`, the DME launcher, not the mod |
| 0x0040317C, 0x004A2677 | Mar 14 | the unlock-all-factions test |

The three .dmp files in `%LOCALAPPDATA%\CrashDumps` are all from 14 March (DME and
unlock-factions), so no region experiment was ever inspected. `scripts/crash_triage.py`
reads new dumps and names the site.

### 7.2 A shipped precedent: the Lord mod's URR sub-mod

`data/campaigns/Lord_main/startpos.esf` has **145** regions against vanilla's 137, with
`CAI_WORLD_REGIONS` still at 205 and `pathfinding.esf` byte-identical to vanilla.
`lord_regions.esf` is vanilla plus eight `settlement_and_slots` records and eight
`region_keys` entries. The eight regions are exactly the dormant-but-complete set from
section 2 of this update: wilderness_mexico, wilderness_tejas, wilderness_great_plains,
wilderness_hudsonsbay, wilderness_canada, wilderness_khiva, wilderness_arabia,
unexplorable. So reactivation is not a theory, it is a mod that runs.

What URR touched in startpos (and therefore what is required): REGION, the owner
faction's `GOVERNORSHIP` region list, `CAI_WORLD_SETTLEMENTS`, one
`CAI_WORLD_BUILDING_SLOTS` item per slot, `CAI_WORLD_REGION_SLOTS` for resource/town
slots, `CAI_WORLD_REGIONS[i]` settlement/slot/region-id links, and
`CAMPAIGN_TRADE_MANAGER/SETTLEMENT_INDICES`. It used invented ids in high bands
(91000000+, 972xxxxxx). It left six of the per-faction BDI arrays at 137 entries, which
means the engine does not require those to match the region count. It also wrote the
eight regions.esf records with their children in a different order, which the engine
accepted.

### 7.3 First build: wilderness_arabia

`scripts/reactivate_region.py --region wilderness_arabia` produces `out/wilderness_arabia/`
from vanilla `regions.esf` and `startpos_vanilla.esf`, using **armenia** (Ottoman, Europe
theatre, tier-1 minor settlement with sheep and town slots) as the template and URR's
positions (validated to be inside the outline and on wilderness_arabia grid cells).
`scripts/verify_region_mod.py` checks every cross-reference and compares record shapes
with URR's: all pass. Both output files round-trip byte-exact.

Known differences from URR, by choice: owner Ottomans (URR: a Lord faction), rebels
`ottoman_rebels`, the sheep slot carries `sheep_peasant_farms` like armenia's, and the
cloned CAI components have their belief/desire id caches emptied instead of pointing at
the template's desire objects. If the first test crashes, that cache choice is suspect
number one and the dump will show it.

Not done yet: localisation (region and settlement names will show as keys), and the
overlay colour is the lookup-TGA palette colour for that area (207,190,106) which also
covers some coast east of the region.

### 7.4 Test procedure

1. `python scripts/deploy_mod.py install --game-dir <ETW> --mod out/wilderness_arabia`
   (backs up the current regions.esf and startpos.esf to `data/_region_mod_backup/`).
2. Start a **new** Grand Campaign. Check Arabia exists on the map, has an Ottoman
   settlement at Tabuk, can be selected, and that an army can walk in and out.
   End a few turns.
3. `python scripts/deploy_mod.py restore ...` before loading any pre-existing save.
4. On a crash: `python scripts/crash_triage.py`, paste the output.

### 7.5 Next regions

The other seven have the same shape. Each needs one `RegionSpec` in
`reactivate_region.py` (positions can be lifted from URR's `lord_regions.esf` records)
and a template region in the same theatre and faction. After that, the "deep compiler"
of section 5 is only needed for regions that do not already exist in the mesh.

### 7.6 First in-game test (2026-09-06 evening): crash on the Grand Campaign button

`crash_triage.py` on the new dump (`Empire.exe.23136.dmp`): fault at Empire.exe+0x4e2165,
the same site as six crashes on 8 and 11 March. Walking the code from the dump:

- the function looks a region name up in a `UTILITYLIB::DATABASE_TABLE<EMPIREUTILITY::REGION_RECORD>`;
- the lookup fails, it formats `"In table %S: '%S' is not a valid key for this table"`,
  sets the index to the table size (0xaf = 175, exactly the row count of
  `db\regions_tables\regions` in patch2.pack), gets a null record, and reads its colour
  bytes at +0xc..+0xe. Null read, crash.

So the frontend, on the campaign button, looks up **every `region_keys` name in
regions.esf** in `regions_tables`. The mod's row was in `wilderness_arabia.pack`, and the
pack never loaded: `deploy_mod.py` had written `user.script.txt` as UTF-8, while every
CA script file (e.g. `preferences.empire_script.txt`) is UTF-16LE with a BOM. This also
explains the March "78th region_keys entry crashes on the GC button" note: the entry was
fine, the DB row was missing.

Fixes: the pack is now PFH0 type 4 (movie), which the engine loads without any script
entry; `deploy_mod.py --script` alternatively converts it to type 3 and writes the
script line in UTF-16. `crash_triage.py` now names this site precisely.

### 7.7 Second test: it works

With the type-4 pack the Grand Campaign loads. Playing as the Ottomans in 1700, the
Arabian desert south of Palestine and Syria is a region with its own borders, an
Ottoman-flagged settlement at Tabuk's position, a town marker and a resource marker.
No labels yet (no localisation rows), as expected. This is the first new campaign
region in ETW produced by this project, and it needed no geometry generation at all.
Still to confirm in play: selecting the settlement, moving an army in and out,
ending turns, AI activity in the region, and saving/loading.

### 7.8 Second campaign test: two findings, both fixed

Played as the Ottomans: Tabuk exists and can be handed over and retaken. Two faults:

**Armies could enter Tabuk but not leave.** Every one of the 77 European capitals and
425 of 428 resource/town slots has boundary records of path_type 7 within two cells;
taw's decoder names type 7 "slot" and type 6 "road". They are the settlement and slot
footprints: per cell, the outline polyline pieces (type 7) and the re-cut passable
pieces (type 0, with `passable_part` = walkable fraction). Path entries 0..3 are
cell-edge markers; the rest index the area's vertex table. Tabuk had none (URR's
regions have none either: Lord's pathfinding.esf differs from vanilla by one byte).
Fix: `src/etwpc/compiler/footprint.py` copies a clean vanilla footprint (Bashkira's
minor settlement, the Pensa wheat slot) translated by a whole number of cells, so the
per-cell clipping, headers and edge markers stay exact, and re-splits only the run
items it touches. The serialiser reproduces vanilla byte-for-byte with no transplant,
and Tabuk's 25-cell block equals Bashkira's record for record. The generator now moves
the capital and slots to the nearest positions that share the template's sub-cell
offset and sit on plain interior cells.

**Crash on end turn after the region changed hands** (Empire.exe+0xbfdd0, a getter on
a null object): `CAI_WORLD_REGIONS[i]` wrapper `OWNED_INDIRECT` is the region's
settlement AI id in all 205 vanilla and Lord entries; the generator had left it 0.
Fixed and now verified.

Data quirk recorded: `i2_ary` maps path ids to regions.esf indices, and taw's name
table equals regions.esf order, but the grid cells tagged with palestine's id lie in
Libya and vice versa (same for bashkira). Harmless for the engine; the code now takes
the template's local pid from its own records rather than trusting the table.

### 7.9 Third test: two crash sites on the first end turn

Three launches, three crashes, two sites.

**Empire.exe+0x757851 (pathfinder, vertex pair through a null pointer).** Caused by the
footprint transplant dropping Bashkira's road records. A road is a thin strip whose
vertices are shared with the passable-area polygons on both sides; in vanilla **no
boundary record anywhere contains a vertex used by only one record** (42,379 records,
every non-marker vertex is used by 2 to 5 records). Dropping the roads produced
single-use vertices and a dangling neighbour. A settlement footprint cannot be lifted
without its roads, and the road network is welded by shared vertices to the whole
region's boundary graph (closure via shared vertices from any European capital covers
1,200+ cells, except islands). Only slot octagons are self-contained (Pensa: 2 cells, 4
records). Fix: the capital gets the same closed slot octagon as a resource slot; the
verifier now enforces the shared-vertex invariant on the output.

**Empire.exe+0x4a2677 (twice).** Per-faction end-of-turn code (caller strings
"FactionTaxRates", "governorship") computes a 0..100 value, and if it is below 50 picks
five random entries from a pool and removes them; the pool had zero entries (edi =
count-1 = 0xffffffff at the crash). The same site fired in the March unlock-all-factions
test. The minidumps hold no heap, so the pool is not yet named. Strong lead: the new
region was 90% Orthodox under Ottoman rule with zero wealth (copied from Armenia),
which is a turn-one revolt, and a revolt picks rebel units from a pool. URR gave its
Arabia the owner's majority religion. Fix applied: 90% Islamic, Palestine's wealth.
Next test should run with full dumps (`LocalDumps\DumpType = 2`) so the object can be
identified if the site recurs.

### 7.10 Fourth test (full dump): the real pathfinder cause

With a full dump the crashed object could be read: a 56-byte edge whose endpoint-node
pointer was null, at world (-32.5, 380.5) on the French Channel coast, one of the
obstacle grid cells startpos lists for rows 118-119 there. Nothing to do with Arabia's
records themselves. The cause: **startpos's `CAMPAIGN_PATHFINDER` stores, for each of
its 9,862 `OBSTACLE_BASE_GRID_NODE` entries, the cell's position in the pathfinding
grid's flat sequence of boundary records + empty cells + word-carrying items** (the same
units as grid_data's count field; verified equal for all 9,862 nodes on vanilla). The
cell-to-node lookup is the u32 pair list next to it. Inserting six sequence entries in
Arabia (rows 28-42) shifted every later cell, so obstacle nodes everywhere after that
resolved to the wrong records. `patch_startpos_pathfinder` now renumbers the node ids
from the new grid (9,817 changed) and the verifier checks all 9,862.

Also learned from the dump: the engine builds runtime edge structures lazily; no edge
existed yet for Arabia's octagon records, only the raw vertex table held them.

The +0x4a2677 crash (empty pool, five random picks) did not recur in this run; whether
the religion fix removed it is still open.

---

## 8. 2026-09-30: openETW, and the fort-obstacle discovery

### 8.1 What openETW is

STRATMAN's video "They're Finally Adding New Regions To Empire Total War"
(youtube.com/watch?v=QimqPqGlNo0) covers **OpenETW** — "Open Empire Total War, Global
Map Platform, Expanding the ETW Map" (moddb.com/mods/openetw). Claims 42 new regions,
20 of them in North America (Delaware, Ohio, Sioux Territory, Rhode Island), Khorasan
added for Persia with Fars/Shiraz next. **Its ModDB downloads page is empty** — the
project is in development and has published no files, so there is nothing to diff.
A separate project, Empire Total War Remake, is also adding provinces; together the
community reports ~70 new regions.

The documented lineage of the technique is the Russian mod **Ultima Ratio Regum (URR)**
-> **Empire Extend**. The community description is: "used already existing void regions
and added cities by using dynamic pathfinding related to forts."

URR is the "URR_" sub-mod already on this disk inside the Lord mod, and it is the
reference this project has been copying since section 7. So our reactivation route is
the same established route, independently rediscovered.

### 8.2 The fort-obstacle trick, measured

`startpos.esf` `CAMPAIGN_PATHFINDER/PATHFINDING_GRID[n]/OBSTACLE_LISTS/FORT_OBSTACLE`:

| | forts in REGION FORT_ARRAY | fort obstacles | obstacles with a real fort within 2 world units |
|---|---|---|---|
| vanilla | 15 | 15 | **14 of 15** |
| Lord/URR | 85 | 85 | **15 of 85** |

So URR added **70 free-standing fort obstacles that have no fort building at all**, and
they sit exactly over the reactivated regions: 9 around `unexplorable` in the East Indies
grid, ~41 scattered across the five American wildernesses, ~20 in the Europe grid
including **(266,201) = Tabuk, 0.07 units from URR's wilderness_arabia capital** and
(427,286) = Khiva. Vanilla capitals have no such obstacle (armenia 63 units away,
palestine 30, france 6.9). They are scattered across each region, not just at the
capital, i.e. a network of navigation anchors.

Crucially URR does this with **`pathfinding.esf` byte-identical to vanilla** (one byte
differs). That is the opposite of what section 7.8 assumed: the static footprint
transplant was the wrong mechanism, and it is what produced the 7.10 sequence-index
crash. The engine's runtime navigation for a settlement in a void region comes from a
fort obstacle in startpos, not from grid records in pathfinding.esf.

### 8.3 Structure of a free-standing obstacle (URR, Tabuk)

```
FORT_OBSTACLE item = [ OBSTACLE(19 children), u32 obstacle_id ]
OBSTACLE:
  [0] BOUNDARIES(6 slots)   only slot[1] used: u32 list, 30 entries, values 0x8000xxxx + a plain index
  [1..4] u32 1,1,1,1        (vanilla real fort: 1,0,0,1)
  [5] MANAGED_OBSTACLE_BOUNDARY(6)  only slot[1]: [True, [obstacle_id+1, 1]]
  [6] u32 7
  [7..10] i32 bbox fixed-point  (260,196)-(272,206)  = 12 x 10 world units
  [11..14] u16 cell range A   URR: 0,0,0,0   vanilla: (127,138)-(133,144)
  [15..18] u16 cell range B   URR: (221,29)-(224,31)  = the bbox in grid cells
  id is also listed in OBSTACLE_LISTS child[4], the parallel u32 id list
```

### 8.4 Revised plan

1. Revert `pathfinding.esf` to vanilla; drop the footprint transplant and the
   sequence-index renumbering (both become unnecessary once nothing is inserted).
2. Add free-standing FORT_OBSTACLE entries to startpos, modelled byte-for-byte on
   URR's, at the settlement and scattered over the region.
3. Keep everything else from section 7 (it is already verified against URR).
4. Open question: how openETW creates regions that are *not* dormant slots
   (Delaware, Ohio, Rhode Island are not void regions), which is still the deep
   compiler problem of sections 2-5.

### 8.5 Implemented (2026-09-30)

**Step 1 — pathfinding.esf is no longer touched.** `patch_pathfinding` (footprint
transplant) and `patch_startpos_pathfinder` (sequence renumbering) are deleted, the
generator no longer emits pathfinding.esf, `deploy_mod.py` no longer installs it, and
the game's copy was restored to vanilla. This matches URR, which ships it unchanged.

**Step 2 — fort obstacles cloned from a donor.** New module
`src/etwpc/compiler/obstacles.py`. `ObstacleSystem` parses the obstacle half of a
startpos PATHFINDING_GRID (the OBSTACLE_BOUNDARIES blob, the grid nodes, the
cell->node pair list, the FORT_OBSTACLE records and their id list).
`clone_obstacle` copies one package from a donor grid, remapping the boundary-entry
indices and the obstacle id, and appending nodes and pair entries. Because
pathfinding.esf is identical between donor and target, every cell sequence index in
the donor's nodes is still correct here, which is what makes verbatim copying safe.

For wilderness_arabia the donor (Lord/URR) yields **three** obstacles, and they land
exactly on the capital and both slots, which is URR's own pattern:

```
962718136 -> 973078592 at (266.00,201.00)  +30 boundary entries, +30 grid nodes
962713976 -> 973078600 at (300.00,222.00)  +36 boundary entries, +36 grid nodes
962713456 -> 973078608 at (290.00,212.00)  +36 boundary entries, +35 grid nodes
obstacles 9 -> 12, nodes 9862 -> 9963, boundary entries 23687 -> 23789
```

Each obstacle also gets its synthetic parked fort object (owner set to ours) and a
cloned CAI_WORLD_FORTS entry.

`verify_region_mod.py` gained obstacle checks: the pair/node counts agree, no duplicate
cells, every node's sequence index matches the vanilla grid, the id list matches the
records, no boundary reference is out of range, and **a fort obstacle covers the
capital and every slot** — the direct test for "trap cell". All pass.

### 8.6 Load crash, and the fort/CAI/obstacle 1:1 invariant

First test of the obstacle build crashed during campaign load at Empire.exe+0x6a7a16,
`inc dword ptr [eax+0x14]` with eax = 8: a refcount bump on a bogus pointer. The
caller does `if (found == end) found = 0; found += 8; AddRef(found)` -- an unguarded
map lookup, so a key was missing.

The full dump's stack named the path exactly: `**CAMPAIGN MODEL LOAD**` (0x43b900) ->
CAMPAIGN_TRADE_MANAGER loader (0x5c9d50, `SETTLEMENT_INDICES`/`PORT_INDICES`) ->
PATHFINDING_GRID loader (0x6b15c0, `BARRIER_NAMES`) -> a DATABASE_TABLE find.

Cause: `add_fort_obstacles` searched **our** startpos for the **donor's** fort id when
cloning CAI_WORLD_FORTS, so the loop never matched and no AI fort records were added.
The result was 18 fort objects and 18 obstacles but only 15 CAI_WORLD_FORTS entries,
and the loader looked up a fort that had no AI record.

Vanilla invariant, now enforced by the verifier:

    fort objects in REGION FORT_ARRAY  ==  CAI_WORLD_FORTS items  ==  fort obstacles

vanilla 15/15/15, URR 85/85/85, ours now 18/18/18. The CAI fort record is cloned from
the donor with a fresh AI id, and its CAI_SITUATED position is set to the obstacle
centre with our region's AI id and theatre list.

### 8.7 Second load crash: donor fort data must not be imported

Rebuilt with CAI_WORLD_FORTS fixed (18/18/18) and it crashed again at exactly the same
place, Empire.exe+0x6a7a16, same caller chain. Reading the map from the full dump
settled it: `this` is not a class with RTTI but a small hash-map struct
(`+0x00` hash fn, `+0x10` bucket count, `+0x14` bucket array) with **1024 buckets, every
one empty** (begin == end). The caller at 0x6a7a20 builds a *string* key and calls the
lookup, which assumes a hit:

    if (found == end) found = 0;  found += 8;  AddRef(found);   // AddRef(8) -> crash

So any lookup into that map fails. It is a name registry that is empty in vanilla, and
our data took a code path that consults it.

Cause: the synthetic forts were cloned from the **donor**, and URR's synthetic forts
carry an empty name key (`FORT[4] == ""`) with a Lord-only localisation string
`names_forts_fort_name_names_X`. Vanilla has no such entry.

Fix: fort objects and their CAI_WORLD_FORTS records are now cloned from **vanilla**
(our own startpos), cycling through the 15 existing forts as templates, and placed at
the obstacle centre with a fresh id and our owner faction. Only the obstacle geometry
still comes from the donor, which is the part we actually need. The generated forts now
look like this:

    oid=973078592 pos=(266.00,201.00) key='818526423'  loc='start_pos_forts_fort_name_818526423'
    oid=973078600 pos=(300.00,222.00) key='1314500845' loc='start_pos_forts_fort_name_1314500845'
    oid=973078608 pos=(290.00,212.00) key='789720503'  loc='start_pos_forts_fort_name_789720503'

Rule of thumb this establishes: **import geometry from a donor, never identity.** Any
string key or localisation id must come from vanilla or from our own DB pack.

### 8.8 Third load crash: the fort garrison army id

Same site again (Empire.exe+0x6a7a16), so the name-key theory in 8.7 was wrong. The
map at the crash is a 1024-bucket hash map with **every bucket empty**, and the caller
assumes the lookup hits, so the real question was which per-fort field we failed to
regenerate.

Diffing all 15 vanilla fort records field by field gives the per-fort identity set:

| field | meaning | we set it? |
|---|---|---|
| `[0]`, `SGR[0].GARRISON_RESIDENCE[0]`, `FORT[6]` | owner faction | yes |
| `SGR[1]`, `FORT[5]` | fort object id | yes |
| `SGR[10..11]`, `FORT[0..1]` | position | yes |
| `FORT[3]`/`FORT[4]` | localisation + name key | from template (shared) |
| **`SGR[12]`** | **garrison ARMY id** | **no — inherited from the template** |

`SGR[12]` is not just a fort field: the same value indexes an `ARMY`, that army's
commander `CHARACTER`, and a `CAI_RESOURCE_MOBILE`. All three cloned forts therefore
claimed the template's army, so the load-time resolution of fort -> garrison collapsed.

The fix is given by vanilla itself: **2 of the 15 vanilla forts carry `SGR[12] = 0`**,
so a fort with no garrison army is legal. Cloned forts now set it to 0. The verifier
checks fort object ids and non-zero garrison ids are unique (18 forts, 18 distinct
object ids, 15 non-zero garrisons, 15 distinct).

This is the third instance of one rule: **cloning a record means regenerating every
field that is unique to it**, and the cheapest way to find those is to diff all the
vanilla instances of that record and treat every field that varies as identity.

### 8.9 Fourth load crash: the obstacle boundary registry (root cause)

Four crashes at the same address meant the hypotheses were wrong, not the details.
Two observations cracked it.

**1. `edx == 0x18a` in every dump.** `edx` is `hash(key) % bucket_count` with 1024
buckets, so the same key failed every time. **2. The hash function is
`mov eax,[ecx]; ret`** -- it returns the key's first dword, so the key is an *integer
id*, not a string. Diffing our startpos against vanilla showed 235 new ids and
**none of them hashes to 394**, so the failing key was a pre-existing id: the map was
simply empty when consulted.

That points at load order. `PATHFINDING_GRID` children are read in this order:

    [2] OBSTACLE_BOUNDARY_MANAGER   <- registers boundary ids, fills the map
    [3] OBSTACLE_BOUNDARIES
    [4] OBSTACLE_BASE_GRID_NODE     <- nodes look ids up in that map
    [6] OBSTACLE_LISTS              <- the obstacles themselves, read last

So a grid node may only reference a boundary id that the manager already registered.
Counting ids confirms it is an invariant:

| | manager ids | referenced by nodes | missing |
|---|---|---|---|
| vanilla | 130 | 130 | **0** |
| URR | 154 | 154 | **0** |
| ours (broken) | 130 | 134 | **4** |

The four were our three new obstacle ids +1, which we never registered, plus
`962713457` -- a **donor** id that leaked through because a copied cell was covered by
two donor obstacles and `clone_obstacle` only remapped the one being cloned.

Fixes: `ObstacleSystem.register_boundary_id` appends a manager entry `[id+1, 1]` for
each new obstacle (vanilla 638 entries -> ours 641), and after all clones a fix-up pass
remaps every donor id in the newly added nodes and drops references to donor obstacles
we did not clone. Ours is now 133 manager ids, 133 referenced, 0 missing, and the
verifier enforces it.

### 8.10 Fifth attempt, and why the obstacle path is parked

Registering the new ids moved the failing bucket from 394 to 358 but did not fix the
crash. Reading the map properly this time (I had sampled only 8 of 1024 buckets before
and wrongly called it empty):

* the map has **478 non-empty buckets and 641 entries**, which is exactly our
  `OBSTACLE_BOUNDARY_MANAGER` entry count (638 vanilla + 3 appended);
* so one runtime object is created per manager entry, keyed by that object's own
  first dword, and the lookup fails for a key no entry produced.

Why appending entries is not enough:

* the manager array is **not index-stable** -- comparing vanilla with URR by shape
  (pair count + flags) rather than by id, only **79 of 638** entries match at the same
  index, so URR's tools regenerate the array rather than patch it;
* in vanilla its **130 distinct ids are spread across 638 entries**, each id appearing
  a median of 7 times (max 39). Entries are therefore *groups* of boundary objects,
  not one-per-obstacle, and URR registered each new id into several existing entries
  (e.g. id 962731657 appears in 20 of them) rather than appending.

So the manager is derived data with a grouping rule that is still unknown, and
`register_boundary_id` appending `[id+1, 1]` is a guess. Working that rule out is the
next real piece of reverse engineering; until then the obstacle path stays parked.

**Shipped instead:** `--obstacles none` builds `out/arabia_playable`, which carries
every other fix (OWNED_INDIRECT, religion/wealth, garrison army id, the DB pack as a
type-4 movie pack) and leaves pathfinding.esf and the obstacle system untouched. Its
only known defect is the original one: armies can enter the settlement but not leave.
The three FAILs the verifier reports for it are exactly that condition.

### 8.11 2026-10-01: the manager rule, found

`OBSTACLE_BOUNDARY_MANAGER` is **exactly the set of distinct boundary lists that the
grid nodes reference**. A list is `(id, flag, id, flag, ...)`; ids are fort obstacle
id + 1 or character obstacle id + 2 (no other kind occurs). Measured per grid:

| | g0 | g1 | g2 | g3 | g4 | g5 | g6 |
|---|---|---|---|---|---|---|---|
| vanilla node lists / manager | 6/6 | 154/154 | 638/638 | 380/380 | 2/2 | 2/2 | 5/5 |
| URR node lists / manager (stale extras) | 54/54 | 207/209 | 631/640 | 214/215 | 0/0 | 60/60 | 0/0 |

Every node list is registered in both files; URR carries a few stale extras, which the
engine evidently tolerates. That explains the 8.10 statistics: an id appears in many
entries because every *combination* of overlapping obstacles over some cell is its own
entry, and the runtime map (one object per entry, keyed by its first dword) is keyed
by the whole list.

The fifth attempt's build had exactly **one** unregistered list,
`(973078609, 1, 973078601, 1)`: a cell where two of our three new obstacles overlap.
`register_boundary_id` only ever added single-id lists.

Fix: `ObstacleSystem.sync_manager()` appends every node list the manager lacks, run
once after all clones and the id fix-up. The fix-up now also drops donor *character*
ids (+2), removes entries left empty, and re-dedupes each node's distinct-list set.
The verifier checks at list level. `out/arabia_manager_fix`: 4 lists registered
(3 single + the combined one), verifier ALL PASS including the three trap-cell checks.

Also measured, and why a wholesale URR transplant is not viable: every
PATHFINDING_GRID also holds 1-125 CHARACTER_OBSTACLEs keyed by character ids that
exist only in URR's startpos, and they share the manager, boundaries, nodes and the
object pool (children [0], [1], [7]) with the forts.

### 8.12 2026-10-01: all eight dormant regions in one build

`reactivate_region.py` now builds any subset of the eight (`--regions`, default all)
into one `out/new_regions/` with one `new_regions.pack`. Facts that shaped it:

* **No vanilla land region is rebel-owned**, so every new region needs a real owner,
  and the template must be one of that owner's regions (its GOVERNORSHIP is reused).
  Owners/templates: Arabia ottomans/armenia, Khiva safavids/persia, Borneo
  netherlands/ceylon, Great Plains plains/great_plains, Tejas pueblo/tejas, Mexico
  new_spain/new_mexico, "hudsonsbay" huron/huron_territory, "canada" inuit/labrador.
* **A FACTION's id is the u32 just before its key string**; the key's index varies
  (ottomans [9], most minors [8]). The old `faction_by_key` read [8]/[9] and only
  worked for the Ottomans.
* **regions.esf flag != pathfinding grid** in general. The flag says which theatre's
  `region_keys` lists the region (vanilla baluchistan: flag 2, theatre india).
  `unexplorable` (Borneo) is flag 2 with its cells and obstacles in grid 0, the East
  Indies grid, which has no other land region; its lookup TGA is 1x1.
* Settlement slots follow the template's layout: persia/ceylon are major cities
  (army-admin, ordnance, government, culture, road, fortification), the rest minor.
* Positions come from URR's regions.esf; geography from them corrected two labels:
  `wilderness_hudsonsbay` is Rainy Lake (vanilla loc "Wilderness (Manitoba)") and
  `wilderness_canada` is Ungava (vanilla loc "Wilderness (Labrador)").
* Overlay colours must be the region's palette colour in `<theatre>_lookup.tga`. URR's
  American colours match; URR's Khiva/Arabia/Borneo ones are 1-pixel colours.
  Khiva is (209,169,73), painted in both the europe and india lookups.
* **Vanilla cell labels for Khiva are odd**: in the europe grid every Khwarezm cell
  carries local id 18 (1035 cells, centroid (397,315)), which the id table maps to
  spain, and Khiva's own id 17 has no cells. URR ships Khiva at these positions on the
  same pathfinding.esf, so the geometry check now hard-fails only outside the outline.
  Open question for the in-game test: does the engine attribute those cells to Spain?
  **Corrected in 9.10:** the id table is read through `grid_data[11]`; pid 18 is Khiva's.
* Localisation: `text\localisation.loc` (vanilla patch_en copy + our keys) ships in the
  movie pack. Keys: `regions_onscreen_<region>`,
  `start_pos_settlements_onscreen_name_<settlement key>`,
  `campaign_map_towns_and_ports_onscreen_name_<town key>`,
  `campaign_map_slots_onscreen_<slot key>`. Whether a movie pack overrides patch_en's
  copy is unverified; failure mode is wrong labels, not a crash.
* Obstacles are cloned per region but the id fix-up and `sync_manager()` run once per
  grid (`GridClones.finish`), so a cell shared by two new regions' obstacles keeps both.

### 8.13 First in-game test of the eight-region build: load crash (2026-10-01)

Crashed on the Grand Campaign button. Full dump: `Empire.exe+0x51470`
(`mov eax,[ecx]; ret`, the integer hash function) with ecx = 0, called from the hash
map `find` at +0x733820. Static call chain: +0x6b2220 (loops over one PATHFINDING_GRID
sub-list, inserting into the grid object's container at +0x8c) -> +0x6a8be0 (per
record) -> +0x6a7a20 (reads an optional reference from the stream; if not found it
leaves the result null) -> the null reference is used as a map key. So this is the
same obstacle-loading family as 8.6-8.10, but a reference that resolves to nothing
rather than an id that is not registered. Checked and consistent in the build: every
node/manager id is an obstacle id (+1 fort, +2 character); OBSTACLE_BOUNDARIES pair
values are geometry, not ids. `out/arabia_manager_fix` (Arabia + obstacles) has never
been run in game, so the bisect is: `out/new_regions_noobs` (8 regions, no obstacles)
and `out/arabia_manager_fix` (1 region, obstacles).

Bisect result (same evening): **both crashed on campaign load**, each at a new site.

| build | what it is | crash |
|---|---|---|
| `new_regions` | 8 regions + obstacles | +0x51470 (dump 8292), analysed above |
| `new_regions_noobs` | 8 regions, no obstacles | +0x64f66f, null read (dump 3280) |
| `arabia_manager_fix` | Arabia only, obstacles + manager fix | +0x6f6b00, null read (dump 2884) |

So there are (at least) two independent problems:
1. The eight-region data crashes even without obstacles. The last build that loaded
   was `arabia_playable` (Arabia only, no obstacles, old code). Suspects, in order:
   the full `text\localisation.loc` in the pack, one of the seven new regions (Borneo
   / Khiva first), the minimal `[False]` LINE_OF_SIGHT, the major-city templates.
   Next: rebuild `--regions wilderness_arabia --obstacles none` with the new code; if
   that loads, add `--no-loc`/regions back one at a time.
2. Obstacles still crash on their own even with the manager rule satisfied, at a new
   site (+0x6f6b00). Walk dump 2884's stack like 8.13 before guessing.

### 8.14 2026-10-02: the three dumps, analysed

Tooling note first: `crash_triage.py` read the exception parameters 8 bytes too early,
so every dump reported "read of 0x0". Fixed. Real fault addresses: 8292 read 0x0,
3280 read 0xc, 2884 read **0x3f800008** (float 1.0 + 8 used as a pointer).

**Dump 2884 (`arabia_manager_fix`, Arabia + obstacles): a grid node points at another
cell's boundary entries.** Stack: grid loader +0x6b3197 -> +0x75a0e0 (walks a 9964-cell
array) -> +0x6e4eb0 (walks each cell's linked list). Cell 944 has no owner object: its
owner pointer is the end sentinel of a 9963-entry map embedded in the grid object, so
the list walk reads a float as `next`. 9964 vs 9963 because two runtime cells carry
the same packed key 0x002600ed = cell (38,237), the cell where our two new obstacles
overlap (node 9892, combined list `[609,1,601,1]`). Its first MANAGED_OBSTACLE_BOUNDARY
lists boundary-entry indices `14192, 14155, 23717`. In URR the same node lists
`14192, 14155, 14156`, all entries **of cell (38,237)**. We cloned obstacle B first
(14156 -> 23717); when obstacle A was cloned, `clone_obstacle` skipped the node
("dst already has a node there"), so 14155 (A's entry) and 14192 (the combined entry)
were never remapped. In our file they point at vanilla entries for cells (96,155) and
(102,152).

Invariant (vanilla 0 violations on all 7 grids, URR 0): **every boundary-entry index in
a node's first MANAGED_OBSTACLE_BOUNDARY is < len(entries) and names an entry whose
cellid is the node's own cell.** The verifier did not check it.

| build | grid | cross-cell refs | out-of-range refs |
|---|---|---|---|
| arabia_manager_fix | 2 | 2 | 0 |
| new_regions | 0 / 1 / 2 | 198 / 418 / 372 | 0 / **59** / 0 |

**Dump 8292 (`new_regions`)** is the same defect: an out-of-range entry index resolves to
nothing, and +0x6a7a20 then hashes the null reference (8.13). Fix for both: remap entry
indices per grid (donor index -> dst index across ALL clones, in `GridClones.finish`),
copy any donor entry a cloned node references but no cloned obstacle owns (the combined
entries), and add the invariant to `verify_obstacles`.

**Dump 3280 (`new_regions_noobs`, 8 regions, no obstacles): AI region graph.** Stack:
+0x623cd0 builds the graph (+0x66af00) then routes (+0x60eb10 -> +0x5bc1b0 ->
+0x64f620). The route is `lower_louisiana -> upper_louisiana -> wilderness_great_plains
-> great_plains`; +0x64f620 looks up both ends in a region-name -> node map at
this+0x170 and the `great_plains` lookup misses. That map holds 143 names (index 132 +
position, REGIONS_ARRAY order): all 145 regions except **great_plains and new_mexico**.
+0x66af00 inserts a region only as an endpoint of an edge record (pair hash
`a*b ^ a ^ 0x4a545eed`, outer container of 56 keys, which matches the faction count),
so those two have no edges.

Why exactly those two: in vanilla's `CAI_WORLD_REGION_BOUNDARIES` (446 records, ids =
CAI_WORLD_REGIONS wrapper item[2]) they are the only live regions whose land neighbours
are all dormant:
great_plains -> {wilderness_great_plains, wilderness_hudsonsbay, great_lakes};
new_mexico -> {wilderness_mexico, wilderness_tejas}. So in vanilla they are probably
absent from the map too, and harmless because no land route reached them. Reactivating
the wildernesses opens such a route, but the map's edge source (still unidentified, not
the CAI boundaries, which already have the edges) has no wilderness <-> template edge.
Not caused by a copied identity field: no CAI/REGION value is shared only by these two
clone pairs. arabia_playable loaded because Arabia does not isolate any live region.

Cheap bisect that tests this: build only the four regions that border neither
(`wilderness_arabia, wilderness_khiva, unexplorable, wilderness_canada`) with
`--obstacles none`. Prediction: loads. Adding any of wilderness_great_plains,
wilderness_hudsonsbay (both border great_plains), wilderness_mexico or wilderness_tejas
(border new_mexico) should bring the +0x64f66f crash back.

### 8.15 2026-10-02: obstacle cloning rebuilt cell by cell

Measured on all 7 grids of vanilla and URR (zero exceptions in both):

* every OBSTACLE_BOUNDARIES entry is listed by **exactly one** grid node, the node of
  the entry's own cell; obstacle slots reference only entries listed under that
  obstacle's id;
* the second MANAGED_OBSTACLE_BOUNDARY of a node is exactly the **set** of the first
  block's id lists (order is hash order, not meaningful); no node lists one id list
  twice; entry order in OBSTACLE_BOUNDARIES carries no meaning;
* entries are **boundaries**, and a multi-id list is the **merged boundary** of
  overlapping obstacles (the "manager"). Entries of one cell share identical pairs when
  obstacles fill it; the second word of a pair carries the cell's pathfinding region
  label in its top bits (0x4800000 >> 22 = 18, Khiva's "spain" label; 0x6800000 -> 26,
  wilderness_great_plains), so entry words reference pathfinding.esf and copy verbatim
  between files that share it;
* grid children [0]/[1]/[7] are a polygon pool (record count / `[n, 2n fixed coords, k]`
  records / free list). Fort obstacle records do not index it; URR's synthetic forts
  use the simple form (flags 1,1,1,1, zero outer cell rect).

The old `clone_obstacle` copied per obstacle and skipped a cell whose node already
existed, which broke the first two rules in 988 places in `new_regions` (+59 indices out
of range). Replacement: `ObstacleCloner` (obstacles.py). `clone()` copies the obstacle
record and records the cells it touches; `finish()` rebuilds each touched cell from the
donor node: entries whose ids were all cloned are copied (one donor->dst entry map per
grid, combined entries included), merges with un-cloned donor obstacles/characters are
dropped (our own entry is complete without them), an existing vanilla node is extended
instead of skipped, the distinct-list block is rebuilt, then slots are patched and the
manager synced. `verify_obstacles` now checks all of the above.

Results (verifier, all 7 grids): arabia_cells 0 problems; new_regions_cells 0;
bisect4_full 0 (old new_regions: 28). Open risk: in grids 0 and 1 our forts share 94 + 54
cells with VANILLA character obstacles (Borneo, Ungava) and there is no merged
(fort+army) boundary for them, because the donor's armies are different ones. Grid 2
(Arabia, Khiva) has none.

### 8.16 2026-10-02: first in-game obstacle test loads; fort objects must be parked

`arabia_cells` LOADED (first obstacle build ever to do so): Tabuk shows as "Tabuk, Jawf"
(localisation works). Defects: a visible fort stood on the capital, both towns, the mine
and the farm; armies could enter each but not leave (no movement mask when selected),
and armies entering the region jumped from Tabuk's outskirts to the mine. Cause: we
placed each synthetic fort OBJECT at its obstacle centre, so it became a real
garrisonable fort and a pathing node. URR parks all 70 of its synthetic forts at
(-342.47, -151.94) (fixed -359108416, -159322832; open South Atlantic) with FORT[2] = 0,
owned by a dummy faction "ballast", and leaves only the obstacle on the settlement.
Fix: `FORT_PARK` in reactivate_region.py (fort position, SGR position and CAI_SITUATED),
FORT[2] = 0; owner stays the region owner (vanilla has no ballast faction).
Builds `arabia_parked`, `bisect4_parked`: verifier ALL PASS.

`bisect4_noobs` / `bisect4_full` loaded but showed no new regions at all, although both
contain Arabia exactly as in `arabia_cells`; the only autosave of the session contains
Arabia alone, so the 4-region startpos most likely was not the one the game read
(continued campaign or a different launcher). To be re-run with a hash check.

### 8.17 2026-10-02: parked forts did not fix the trap; the 4-region crash is a missing route

Retest: `arabia_parked` loaded with no visible forts, but armies still could not leave
and still "teleported"; part of the region sits under fog of war. `bisect4_noobs` and
`bisect4_parked` both CRASHED on load at the same new site, Empire.exe+0x596e02 (dumps
21452, 772), so obstacles are not the cause.

Dump 772: the engine's transport graph has 287 nodes (0-131 ports, 132-270 regions in
REGIONS_ARRAY order, great_plains and new_mexico absent again, 271-286 unnamed) and a
pair map of ~11,584 node pairs holding paths. +0x596c10 walks required pairs and looks
each up; **(199 labrador, 270 wilderness_canada)** has no path, so the lookup returns
null. The required pairs are owner-adjacent region pairs (Ungava's owner inuit also owns
labrador; same shape as 3280's great_plains). URR loads with the same kind of pairs, so
the difference is that our new regions have **no path in or out**: void regions carry
no settlement/slot footprint in pathfinding.esf, which is also exactly the in-game trap
(arabia_playable, arabia_cells, arabia_parked all trap, with or without obstacles).
Checked and ruled out: URR's one-byte pathfinding.esf change (grid 0 grid_data[2]
64528 -> 1040, East Indies header only); regions.esf roads/railways/canals (empty in
vanilla, URR and ours).

Fix (back to the 7.8-7.10 route, which was never tested after its last fix):
`--footprints` (reactivate_region.py `place_footprints`): each capital, town and
resource slot is snapped to the nearest clean interior block (template + whole cells,
inside the outline, avoiding cells with startpos obstacle nodes, search ring 6 then 12)
and gets Pensa's wheat-slot octagon (`FOOTPRINT_TEMPLATE`, 2 cells / 4 records),
copied across grids (`AreaGrid.transplant(src=...)`; all grids share the even 2-unit
lattice). settlement_* building slots move with the capital. Then every startpos node
sequence id is renumbered and out/pathfinding.esf written; deploy_mod installs and
restores pathfinding.esf. Requires `--obstacles none` (obstacle entries describe cell
geometry). Verifier: each capital/town/resource point is leavable (footprint or
obstacle) and no single-use boundary vertex beyond vanilla's own (grid 0 has 3, grid 4
several, all path_type 3 grid edges).

Builds: arabia_fp, bisect4_fp, new_regions_fp. All capitals footprinted; 6 minor slots
without a clean spot (Borneo 5, Ungava 1) are left as plain ground. Verifier ALL PASS
except those slots. Untested in game.

### 8.18 2026-10-02: footprints fix the trap; Labrador is the next blocker

**`arabia_fp` works in game**: armies enter and leave Tabuk and its slots, no teleporting.
The settlement footprint (8.17) was the missing piece all along; obstacles are not needed.

`bisect4_fp` still crashed at +0x596e02 (dump 13288), again on the leg
`wilderness_canada -> labrador`. The ~11.5k-entry pair map is the engine's transport-graph
EDGE list, not all-pairs paths: vanilla regions have edges to their land neighbours plus
their own ports (syria: anatolia, mesopotamia, palestine + 3 ports; labrador: its 2 ports
+ newfoundland only), while all four new regions have edges to **all 132 ports and no
land neighbour**, i.e. they are treated like sea nodes. The crashing request was a
single leg (canada -> labrador) that has no edge.

Ruled out as the edge source: pathfinding border groups (`grid_data[11]`: 85 path ids then
`[n, ids...]` groups, 1-based; they already list wilderness_arabia-syria,
wilderness_great_plains-great_plains, ontario-wilderness_canada etc.); regions.esf region
type (all `land`); the grid pool in startpos PATHFINDING_GRID[0/1/7] (obstacle outline
polygons `[n, n points, k]`, freed slots `[0, 0]`, not routes).

URR sidesteps it by ownership: Labrador -> **France** (Ungava inuit), new_mexico -> spain
(wilderness_mexico pueblo). Labrador, great_plains and new_mexico are the vanilla regions
whose only land neighbours are void regions, so they have no land edge; giving one owner
both such a region and the adjacent new region makes the AI request a leg that cannot
exist. (URR does keep plains owning great_plains + wilderness_great_plains; unexplained.)
Open: what builds the land edges, so new regions can get real ones.

Build `bisect3_fp` (arabia, khiva, unexplorable): verifier passes except 3 unplaced
Borneo slots. Untested.

### 8.19 2026-10-03: Borneo and Khiva work; transfers, minimap, coastal slots

`bisect3_fp` (arabia, khiva, unexplorable) loads and plays: all settlements and slots
leavable, except Borneo's town Tabanio (trap + teleport), which had no footprint, and
Khiva is missing from the minimap.

* **Coastal slots**: Tabanio's neighbourhood is mostly sea-labelled (label 1), so the
  per-point label made `find_clean_target` look for a clean *sea* block. Slots now try
  the capital's land label first, then the local majority. Tabanio placed; Sumba
  (Borneo) and Mistassini (Ungava) still have no clean spot.
* **Minimap**: vanilla `europe_lookup.tga` (490x300, 8-bit colour-mapped, bottom-left
  origin, theatre bounds x -180..440, y 140..520 from regions.esf theatre[1..2]) paints
  3,553 of Khwarezm's 3,922 pixels with the void colour (0,63,0), not a region colour.
  `repaint_lookup` paints a new europe-theatre region's void pixels with its palette
  colour (Khiva = index 126) and ships the TGA in the pack as
  `campaign_maps\global_map\europe_lookup.tga`.
* **AI ownership invariant**: every region's CAI id is in its owner's `CAI_FACTION[0]`
  and its `CAI_GOVERNORSHIP[3]` (France 8/8). New regions were never added; now they are
  (`cai_own_region`). CAI_FACTION: [0] owned CAI regions, [5] and the int after
  CAI_BDI_NEW_TURN = capital CAI region, [6] faction id, [10] CAI governorship ids.
  CAI_GOVERNORSHIP: [startpos governorship id, theatre, ?, [CAI regions]].
  CAI_WORLD_SETTLEMENTS: wrapper OWNED_DIRECT[0] = owner CAI faction id,
  CAI_SETTLEMENT[1] = owner CAI faction id only on the faction's capital.
* **Transfers** (user choice = URR's): `TRANSFERS` / `transfer_region` move Labrador to
  France (governorship of new_france; Inuit's capital fields FACTION[35..36] and CAI
  capital -> Ungava) and new_mexico to Spain (governorship of florida). Touches REGION
  [19]/[21], garrison residences, startpos GOVERNORSHIP lists, CAI governorship and
  faction lists, CAI settlement owner/capital marker. Neither settlement has a garrison
  army. `--no-transfers` keeps vanilla owners. Sioux Lands / great_plains both stay
  Plains (as URR) - the remaining known risk.

Builds: bisect4_fpt (arabia, khiva, unexplorable, canada), new_regions_fpt (all eight).
Verifier: ALL PASS except the two unplaced slots.

### 8.20 2026-10-03: the transport graph is CAMPAIGN_TRADE_MANAGER (load crash fixed)

`bisect4_fpt` (arabia, khiva, borneo, ungava + Labrador->France) LOADS and plays; Khiva
now shows on the minimap. `new_regions_fpt` crashed at +0x64f66f again (dump 20732):
route lower_louisiana -> upper_louisiana -> wilderness_great_plains -> great_plains with
great_plains absent from the AI region graph.

The engine's transport graph is stored in startpos `CAMPAIGN_TRADE_MANAGER`:

| child | vanilla | meaning |
|---|---|---|
| PORT_INDICES | 132 | `[port key, node id]`, ids 0..131 |
| SETTLEMENT_INDICES | 135 | `[region, node id]`, ids 132..266; **great_plains and new_mexico absent** |
| TRADE_NODES | 20 | `[pos, node id]` sea waypoints, ids 267..286 |
| TRADE_ROUTES | 11,584 | `[a<b, [segment refs], length]` = the pair map in the dumps; land links between neighbouring settlements (60 of 228 have no segments), port<->port sea lanes |
| TRADE_SEGMENTS | 1,737 | polylines the routes are made of |

Two bugs followed: (1) `patch_startpos` gave new settlements node id max+1 = 267.., i.e.
**the sea waypoints' ids** (hence "edges to all 132 ports" in 8.18); (2) no land links,
and great_plains / new_mexico never were nodes. URR's fix: waypoints moved to 411..430,
settlement nodes for all 8 + great_plains + new_mexico (ids 267..274), 2 new ports with
8 sea-lane segments (2 x 154 routes), and segment-less land links of length 227.27 to
every land neighbour (23).

Ours (`patch_trade_network`): shift the 20 waypoints above the new ids (rewriting route
endpoints), add settlement nodes for the new regions and for any settled vanilla region
missing from SETTLEMENT_INDICES that gains a settled neighbour, and add a segment-less
link to each settled CAI_REGION_BOUNDARY neighbour, length = straight distance between
the settlements. All eight: 10 nodes, 26 land links (incl. labrador-wilderness_canada).
Verifier: settlement node exists, does not alias a waypoint/port, >= 1 land link.
Build: `new_regions_trade`. The ownership TRANSFERS stay (user choice) though the links
may make them unnecessary.

**Banjar tooltips** on the Canary and NW Siberian islands: vanilla `unexplorable` is a
catch-all of 61 areas (Canaries = areas 0-4, Novaya Zemlya = 6-7, Borneo/Indonesia =
9-25, ...); activating it names all of them. Area flag [2] is "passable" (France's
mountain sub-areas are False), not a tooltip switch. Fixing it needs those areas to
belong to another region record; all 205 are used, so the Canary Islands as a region
means a genuine 206th region (regions.esf + every per-region parallel array).

### 8.21 2026-10-03: `new_regions_trade` crash, node ids must never move

`new_regions_trade` crashed on load at Empire.exe+0x66b08b (dump 1520, null read of
`[eax+0x1c]`). Shifting the 20 sea waypoints up by 10 broke stored trade routes:
`DOMESTIC_TRADE_ROUTE [6]/[7]` and `INTERNATIONAL_TRADE_ROUTE [3]/[4]` name nodes by id,
so three domestic routes (e.g. Portugal's to waypoint 273) now ended at new settlements.
Rule: **existing transport-graph ids are never renumbered**; new settlement nodes go
above every id (287..296). Node ids are map keys only (URR runs them out of order).
Borneo has no settled land neighbour, so it gets one segment-less link to the nearest
sea waypoint (283) instead of being an isolated node.

Build `new_regions_trade2`: compared with vanilla, 0 node ids change meaning, 0 vanilla
routes change, and all 75 stored trade routes resolve to the same endpoints (the crashed
build fails that check with 3). Verifier ALL PASS for all eight except the two known
unplaced slots (Sumba, Mistassini). Untested in game.

#### Test procedure (Git Bash)

The user runs everything in Git Bash. The scripts live in the Documents clone; the venv,
the builds (`out/`) and the game data live in the game-dir clone.

```bash
# setup (once per shell)
GAME="/x/Games/Steam/steamapps/common/Empire Total War"
REPO="/c/Users/Dan/Documents/projects/etw-region-compiler"
cd "$GAME/etw-region-compiler"
export PYTHONPATH="$REPO/src"
PY=.venv/Scripts/python.exe
MOD=out/new_regions_unlocked

# install, check, then start a NEW Grand Campaign in game
$PY "$REPO/scripts/deploy_mod.py" install --game-dir "$GAME" --mod $MOD
$PY "$REPO/scripts/deploy_mod.py" status  --game-dir "$GAME" --mod $MOD

# after a crash
$PY "$REPO/scripts/crash_triage.py" --game-dir "$GAME" --last 1

# always before loading an existing campaign (Norway)
$PY "$REPO/scripts/deploy_mod.py" restore --game-dir "$GAME" --mod $MOD
```

#### Result and next build

`new_regions_trade2` **loads** with all eight regions (user, 2026-10-03). The regions
owned by non-playable factions could not be checked, so `new_regions_unlocked` =
trade2 with `unlock_factions.py --src/--dest` applied to its startpos.

First attempt: flipping CAMPAIGN_PLAYER_SETUP[4] ("Playable") on 45 factions changed
nothing; the faction list stayed at the 11 majors. Field-by-field diff of vanilla
France / vanilla Plains / the Lord mod's (playable) Plains: playable factions carry an
optional FACTION-level `CAMPAIGN_VICTORY_CONDITIONS` record, flagged by the bool just
before it (minor: `religion, False, FORT_UPGRADE_MANAGER`; major and every Lord faction:
`religion, True, CAMPAIGN_VICTORY_CONDITIONS, FORT_UPGRADE_MANAGER`). Its content is
identical to CAMPAIGN_PLAYER_SETUP[0]. Not needed (absent on Lord's playable France):
CAMPAIGN_MISSION_MANAGER, CAMPAIGN_SHROUD. `unlock()` now also sets that flag and
inserts a copy of the setup's victory conditions, and skips factions with no capital
(int after the last FORT_UPGRADE_MANAGER == 0: united_states, punjab, mamelukes, ...;
`--all` overrides). Result: 42 playable (11 + 31), round-trip PASS. Untested in game.


Second attempt (startpos flag + victory conditions) also left the list at the 11 majors.
The front end (`ui/frontend ui/grand_campaign_scripts/main_panel.luac`, a flag carousel
fed by `FrontEnd:CampaignDetails().Factions` sorted by `ListOrder`) is gated by the
**DB table** `db/factions_tables/factions` (newest: patch2.pack, 95 rows). After key,
u32 id and 6 strings (culture, category, name, adjective, name group, flag key) each
row has 4 one-byte flags: `00 00 01 01` on exactly the 11 majors + united_states,
`00 00 00 00` on every minor (mughal too, though its category string is 'playable'),
`00 01 00 00` on the 26 rebel rows, `00 00 00 01` on pirates. `unlock_factions.py
--game-dir --pack` copies the newest table, sets flags [2],[3] = 01 01 on the unlocked
factions (61 bytes for 31 rows) and adds it to the mod pack under the same path, so it
replaces patch2's. Build: `new_regions_unlocked` (startpos + pack). Untested.

**Correction, third attempt (2026-10-03).** The DB table build did not work either, and
the DB theory is wrong: the user's live startpos (53 MB, DarthMod campaign
`DMUC_Umain`, restored by deploy_mod) makes Norway selectable with the stock table,
where Norway's flags are `00 00 00 00`. The DB change was removed from the script.

The selection screen reads **`CAMPAIGN_PREOPEN_MAP_INFO`**, the summary block at the top
of startpos (before CAMPAIGN_ENV), not the FACTION records. Vanilla vs DMUC for Norway:

| preopen field | vanilla Norway | DMUC Norway |
|---|---|---|
| `PLAYERS_ARRAY[i]` CAMPAIGN_PLAYER_SETUP[4] | False | True |
| `FACTION_INFOS[i]` = (key, portrait, A, B, n, desc, flag, regions) | `'', False, False, .., 0` | portrait, True, True, .., 1 |
| `VICTORY_CONDITION_OPTIONS` entry | none (11 majors only) | 4 conditions on `norway` |

`B` is the selectable flag: vanilla Mughal is `True, False` and not selectable, DMUC
Mughal `True, True`. DMUC also gives iroquoi/cherokee VC entries and a playable
PLAYERS_ARRAY flag but leaves A/B False. `unlock_factions.py` now sets all three for each
minor with land (pirates excluded), borrows a portrait where empty (thirteen_colonies,
louisiana, chechenya_dagestan), and builds VC entries as DMUC's Norway's
(`(F,1799,25,F,1) (F,1799,25,F,3) (F,1750,15,F,0) (F,1799,20,T,2)` over the faction's
starting regions, new regions included). 30 factions, 41 VC options, round-trip PASS.
Not yet updated: FACTION_INFOS[7] region counts and REGION_OWNERSHIPS_BY_THEATRE (the
front-end ownership map) for the new regions and the two transfers. Untested.

**Result:** `new_regions_unlocked` works in game (user, 2026-10-03): the minors are
selectable and all eight regions play.

## 9. 2026-10-03: the Canary Islands as a true 206th region

User decisions (memory etw-canaries-decisions): append a **new** region record rather
than repurpose an unused one; Canaries = `unexplorable` areas 1-4 (x -121..-95,
y 197..208, Europe grid 2), owner Spain, capital San Cristobal de La Laguna; Madeira
(`unexplorable` area 0, x -123..-118, y 231..234) moves into Portugal's record. No file
on disk has 206 regions (vanilla, Lord/URR, DarthMod: 205).

### 9.1 What is sized or indexed per region

Arrays of exactly 205 (scan of all three vanilla files): regions.esf
`region_data/regions`; startpos `CAI_WORLD_REGIONS`, `CAI_BDI_COMPONENT_BLOCK_OWNS`
(one block), `CAI_BDI_POOL_DESIRES` record, `CAI_ANALYSER` u4[205],
`CAI_WORLD_RESOURCE_MOBILES` u4[205] x2 (may be coincidence). pathfinding.esf has none.
Index references (not lengths): regions.esf quadtree segments `(region, area)` on both
sides and per-leaf defaults (3.4); pathfinding `grid_data[2].i2_ary` path id -> regions
index (85 entries) + interior runs / boundary records tagged with path ids (3.1-3.2);
CAI HLCIs (one per mesh area); DB `regions_tables`, `europe_lookup.tga` palette.

Moving areas out of `unexplorable` shifts its later area indices, so every
`(unexplorable, area)` reference (quadtree, HLCIs, any area-index field) is remapped,
not just the moved ones.

### 9.2 Plan: make it dormant first, then reactivate it

1. **M1, 206 records, dormant.** regions.esf: append record 205 `canary_islands`
   (type land, theatre flag -1 like the void regions) holding areas 1-4; remap the
   quadtree; append a CAI_WORLD_REGIONS entry and extend the other 205-arrays. No
   settlement, no pathfinding change. Load test answers "is 205 hardcoded?" cheaply.
2. **M2, pathfinding + Madeira.** New path id (86th `i2_ary` entry -> index 205);
   relabel the islands' interior runs and boundary records; Madeira's area and cells
   to Portugal. Load + move-a-ship test.
3. **M3, reactivate.** The 206th record is now a dormant region like the eight, so
   `reactivate_region.py` gives it a settlement (Spain, La Laguna), footprints,
   transport node, DB rows, lookup colour (needs a free palette slot), preopen entries.

### 9.3 M1 build and the first 206-region crash

`scripts/add_region.py` (M1): regions.esf record 205 `canary_islands` (dormant copy of
`central_italy`, areas = unexplorable 1-4), Madeira -> portugal area 2, unexplorable 61 ->
56 areas; packed `region<<16|area` references remapped (36 quadtree leaf defaults, 2,935
segment sides, 131 outline connectivity entries). startpos: CAI_WORLD_REGIONS[205] (ai id
from IdPool, BDI caches cleared), HLCIs 66-69 retargeted / 65 to portugal / the rest
renumbered, boundary 6909 (unexplorable-atlantic_ocean_e) handed to the new region,
THEATRE 34 [3] membership. No DB row (dormant regions have none: regions_tables has
158/175 rows; wilderness_*, central_italy absent).

`canaries_m1` crashed on Grand Campaign launch at **Empire.exe+0x8ada40** (dump 6380,
`mov eax,[ecx+0x10c]`, ecx = 0). Trace: a CAI region object (key [obj+0x20] = 91000124,
the new region) looks itself up in a hash map (manager+0xfc, `key ^ 0x4a545eed % buckets`,
20-byte buckets, nodes `[prev,next,key,value]`) holding **205** entries keyed by CAI region
ai id; values are per-region analysis objects. Its source:

* `CAI_INTERFACE/CAI_BDI_POOL` desire id **11** owns exactly one belief per region:
  BLOCK_OWNS `[belief id, 11, 0.0, 0.0, 1]` x205, count [17] = 205, id list [19],
  CAI_ANALYSER [0] = ids, [1] = `(region ai, belief id)` pairs. Vanilla ids 7638..7842 in
  region order.
* each belief: type 146, [10] = `[11, region index]`, `CAI_BORDER_PATROL_ANALYSIS`
  `[region ai, SPECIFIC_AREAS[(AREA_SPECIFIC[region ai, area index, PATROL_POINTS[(x, y,
  [neighbour region ai])], x, y])]]` - per-AREA entries, so they move/renumber with areas.

The other 205-counts are coincidences (REGION_RECRUITMENT_MANAGER = 137 regions + 68
slots; the 205-item CAI_INTERFACE_MANAGERS desire pool mixes desire types). M1 bug also
fixed: the AI position was the centre of unexplorable's remaining bbox (671.9, 119.6).
Build `canaries_m1b` adds the belief (id 91000125, desire 11 -> 206; area entries: 4 to
the Canaries, 1 to portugal, 21 renumbered). Untested.

**Result:** `canaries_m1b` LOADS (user, 2026-10-03): 206 regions work, so the region count is
not hardcoded. Islands have no tooltip and the region shows blank in the Lists panel (no
loc/DB yet); units can land and move on them (cells still carry unexplorable's path id).

### 9.4 M2: a new path id in the Europe grid

Path id space of a pathfinding grid with n regions (verified on all 7 vanilla grids:
sea == n everywhere): `0..n-1` regions (`grid_data[10]` i2: pid -> regions.esf index),
`n` = sea, `n+1..` = one id per border group (Europe: 86..268 for the 183 groups, e.g.
110 gibraltar/portugal border cells), `1022/1023` edge markers. Interior run and
zero-record header cells (T_U2) carry a region pid or n; boundary records of every type
share the space (type 0/6/7 regions + borders + sea, types 1/4/5 only n, 2/3 1023). Type-0
ids < n agree with a neighbouring interior cell's pid 93% of the time.
`grid_data[8]`, `[9]` = n (u16). `grid_data[11]` u2 = n 1-based pids in an order close to,
but not exactly, the regions' southern edge (ties and a few swaps unexplained), then the
border groups of 1-based region pids (**wrong, see 9.10**: per pid its 1-based i2 slot, and
the groups hold i2 slots). startpos `CAMPAIGN_PATHFINDER/PATHFINDING_GRID[g]`
item [8] = one bool per path id (268 in grid 2); its 173,278-word list after the path
count holds obstacle outline polygons in fixed world coords (not path ids).

Islands and Madeira: every land cell is a header cell; their land records are type 0
with unexplorable's pid 7. M2 (`add_region.py --pathfinding-esf`): sea and border ids +1
(8,334 cells/records), island records 7 -> 85 (19), Madeira 7 -> portugal's 22 (6), i2
+= [205], counts 86, order list gets 86 at slot 8 (by southern edge), startpos flags
269 (new entry copied from pid 7). Cell structure unchanged, so obstacle node sequence
ids are untouched. Build `canaries_m2`. Untested.

**M2 result:** `canaries_m2` loads; Canaries and Madeira are navigable, but fleets could not
pass the **Strait of Gibraltar**. Cause: startpos `OBSTACLE_BOUNDARIES` entries
`[n, (a, b) * n, cellid, 0]` are per-cell copies of boundary records (same packing, same
path id space) for every obstacle-touched cell; they kept sea = 85, now the Canaries' id,
around the Gibraltar fort. `canaries_m2b` shifts them too (15,269 records; none on
unexplorable's pid). Rule: a path-id renumber must cover pathfinding.esf AND every
startpos OBSTACLE_BOUNDARIES entry of that grid.

**M2 result:** `canaries_m2b` - everything navigable, Gibraltar open (user, 2026-10-04).

### 9.5 M3: the Canaries settlement (no footprint yet)

Pipeline for 206 regions: `add_region.py` on the vanilla files (`out/canaries_base`:
dormant record + M2 relabel) -> `reactivate_region.py` for all nine regions in one pass
(one pack, one localisation.loc) -> `unlock_factions.py`.

`reactivate_region.py` additions: `RegionSpec.positions` (explicit capital/slot points
for regions URR lacks), `RegionSpec.footprints` (per-region opt-out), a new region's
lookup colour claims a palette entry no pixel uses (from the top: 254; vanilla
europe_lookup uses 150 of 256 entries) and paints every pixel inside its outline (the
islands are neutral grey-green 152/153, not void), `LOOKUP_GIFTS` paints Madeira's 7
neutral pixels portugal's colour. Spec: template `naples` (Spanish, minor settlement,
has town and wine slots; sardinia has no town slot), owner spain, culture
sc_european_south, emergent `spanish_rebels`, La Laguna (-115.50, 202.38, NE Tenerife),
town Las Palmas (-109.86, 199.57), wine Lanzarote (-96.73, 206.54); trade node 295 with
a link to sea waypoint 272.

Footprint: deferred (user choice). Vanilla island settlements (Malta) put the slot
outline (type 7) inside coastal header cells whose records partition the cell (types
0 land, 1 sea, 2 impassable, 6 ?, 7 slot; corners E0..E3; per-record passable byte and
20-bit field, 8-byte cell header - semantics unknown), so the interior-block transplant
cannot be used and a whole-island Malta copy would reshape the coastline. The test asks
whether a capital inside existing coastal land records traps armies at all.
Build `canaries_m3_unlocked`. Untested.

**M3 result:** `canaries_m3_unlocked` loads; the first end turn crashes at
**Empire.exe+0x8df200** (dump 22468): fn +0x8df140 builds a list of region-group
analyses from an object, then for each entry of `this` (+0x10c count, +0x110 data) finds
`[entry+0xfc]` in that list and erases it with no not-found check -> erase(end) runs off
the heap. `this` = belief 18267 CAI_REGION_TARGET_PATHS_ANALYSIS (type 340) -> 18268
CAI_RTPA_REGION_GROUP_INFO (341) -> 7399 CAI_BASIC_REGION_GROUP_ANALYSIS (type 81 =
Austria's group: austria, bohemia, hungary, croatia, ...; unchanged from vanilla). The
live list held a runtime-created group (id 80692) instead: the end turn rebuilt the
region groups and a target-path analysis kept the old group id. Neither the eight
reactivations nor this build add new regions to region groups. Bisect build
`bisect_dormant206_unlocked` = same pipeline with canary_islands left dormant.

**Bisect:** `bisect_dormant206_unlocked` ends turns cleanly, so settling the Canaries is the
trigger. Cause: a CAI_WORLD_REGIONS wrapper's [21] lists the region's memberships, and
`clear_bdi` had emptied it for the new region:

* `CAI_BASIC_REGION_GROUP_ANALYSIS` (belief type 81), owned by desire **5** (113 groups;
  BLOCK_OWNS `[id, 5, 0.0, 0.0, 1]`, count [17], ids [19], CAI_ANALYSER [0] ids and [1]
  205 `(region ai, group)` pairs - every region maps to a group). Group record: [0] core
  regions, [1] neighbour regions, [2] neighbouring CAI factions, [3] owner CAI faction
  (0 = unowned), [4] flag, [5] float, [7] centre, [8]/[9] bbox w/h. Per owner, contiguous:
  portugal's 7388 = [portugal] / [atlantic_ocean_e, spain]; dormant central_italy's 7400
  = [central_italy] alone. Belief [10] = [desire, index in its BLOCK_OWNS], [20] = [region
  ai]. Banjar stayed in its unowned groups after the Dutch took it and that plays, so a
  region in the "wrong" group is tolerated; a region in none is not.
* `CAI_REGION_OCCUPANCY_ANALYSIS` (type 159), desire **20** (189), same registration;
  record [0] = region ai.

`add_region_memberships` clones the dormant template's group (one-region, centre and
extents = the new bbox) and occupancy beliefs and registers them (desire 5 -> 114,
20 -> 190). Still empty on the new region: wrapper [10]/[14], ~15 per-manager beliefs
(CAI_REGION_MILITARY_STRENGTH etc. in the CAI_INTERFACE_MANAGERS pools). Build
`canaries_m3b_unlocked`. Untested.

**M3b result (user, 2026-10-04):** `canaries_m3b_unlocked` - no crash over several end
turns, but **armies are trapped in La Laguna** (an agent/priest can enter and leave).
So a capital inside existing coastal land records is not enough: the settlement needs a
slot outline (type 7) in the pathfinding grid, as on land.

### 9.6 What a coastal footprint needs (open)

* Whole-island transplant (Malta) is out: the Canary islands touch cell-to-cell (area 0 and
  1 share column 33, areas 2 and 3 row 32), so no template block with a pure-sea ring fits.
* "Squaring" the island into interior land cells is out: vanilla never has a land run cell
  next to a sea run cell (0 of 23,436 land-run neighbour pairs); coasts always live in
  header cells.
* Generating records in place: a cell's records partition it (types 0 land, 1 sea, 2/3
  marker 1023, 6 ?, 7 slot), vertices + corner markers E0..E3. The 8-byte cell header looks
  like terrain samples (interior run cells carry the same patterns: 57ff57ffff57ff57 x6,545,
  ffffffffffffffff, 5757..., 1f1f...), so an edited cell can probably keep it. Per record,
  `passable_part` (8 bits) and `unknown2` (20 bits) are shared across types (241/4113 is the
  top value for both type 0 and type 1: 708 and 583 records) and so describe the polygon's
  place in the cell, not terrain - but they are NOT simply "half-edges covered" (per-bit
  agreement 0.39-0.82 over ~1,800 records). Next: fit them against richer features
  (corners included, edge intervals, vertex order), or read the engine's loader.

**M3b checks (user):** tooltip "Canary Islands", Lists panel, minimap colour, Madeira as
Portugal, La Laguna / Las Palmas / wine slot all correct; only the army trap remains.

### 9.7 Decoding the cell records (progress, 2026-10-04)

* **Corner markers** in a record's entry list: `E0 = (0,0)` bottom-left, `E1 = (0,2)`
  top-left, `E2 = (2,0)` bottom-right, `E3 = (2,2)` top-right, in cell-local units. With
  this mapping 99.9% of 14,021 sampled records are valid polygons inside their cell (other
  permutations ~51%). Records are closed polygons, not polylines.
* **`passable_part` (8 bits) = 8-neighbour connectivity mask**, ~99.99% over 15,338
  records: bit0 right edge, bit1 corner E1, bit2 top edge, bit3 corner E3, bit4 corner E0,
  bit5 bottom edge, bit6 corner E2, bit7 left edge (set when the polygon touches that edge /
  contains that corner, i.e. which neighbouring cells it can reach).
* **`unknown2` (20 bits)**: five nibbles; nibbles 0/1/3/4 line up with bottom/left/right/
  top (zero when the polygon does not touch that edge, values 1-5), nibble 2 ranges 0-14
  (type 0 mostly 0 or 2). "Number of separate stretches along the edge" fits only 42.5% of
  47,798 records (grids 1+2), and "index of the neighbour cell's matching record" does not
  fit the samples either. Still open, as are the 8 header bytes (run cells share the same
  patterns, so probably terrain samples).

Plan: when a generator reproduces passable_part, unknown2 and the header for every vanilla
header cell from its polygons alone (100% self-test), use it to cut a slot octagon (type 7)
out of Tenerife's land record in place.

### 9.8 Coastal footprints: encoding decoded, generator built

`unknown2` decoded: five nibbles `[bottom, left, inner, right, top]`. `inner` = bitmask over
the ranks of the cell's records of the polygon's class that share a boundary stretch with
it; an edge nibble = the same kind of mask over the neighbour cell's records whose stretch
along the shared edge overlaps this polygon's (1 if the neighbour is a run cell, 0 if the
polygon does not touch that edge); masks keep 4 bits. Classes: land {0,6,7}, sea {1,4,5,7},
type 7 ranks over all live records (a slot outline is reachable from land and sea); dead
{2,3} have pass 0 / unknown2 0. Over grid 2's 31,126 live records the rules reproduce
both fields for 99.25% (grid 1: 97.0%; misses mostly types 4/5/6). Around Tenerife: 117 of
117 cells reproduced exactly. Rings run counter-clockwise. The Pensa template confirms it:
slot outline = octagon straddling a cell edge, one type-7 half + the land remainder per
cell, headers unchanged (terrain).

`src/etwpc/compiler/coastal.py`: `CellView` decodes cells, `compute_fields` /
`self_test` apply the rules, `find_site` finds the nearest cell edge (horizontal or
vertical) where an octagon fits inside one land record of the region's path id in both
cells (margin tested on the grown half, since the half sits on the shared edge),
`cut_footprint` splits it into the two type-7 halves, subtracts it from the land records,
re-encodes polygons (corner markers, shared fixed-point vertices) and recomputes the
fields of the two cells and their 4-neighbours. `reactivate_region.py` uses it for
`RegionSpec.footprints = "coastal"`, refusing to edit if the self-test fails near a site.
Note: the pathfinding coastline does not match the regions.esf outline closely (Tenerife's
walkable land is x -120..-114), so sites are searched around the desired points.

Canaries (`canaries_m4_unlocked`): La Laguna (-116.3, 202.0), Las Palmas (-110.0, 199.3,
vertical edge), resource moved to Fuerteventura (wheat, -100.0, 201.4, small octagon;
Lanzarote has no room). Untested. (The octagon cutter is superseded by v2, 9.11; the field
rules above are unchanged.)

### 9.9 `canaries_m4` result and the next session's plan (2026-10-04, night)

**Result (user):** no crashes. Armies **created in** La Laguna can leave and re-enter (the
coastal footprint works). But (a) **troops can no longer be landed on the islands** from
ships (worked in m2b/m3b), and (b) **armies cannot enter Gibraltar or the ports next to
it** unless they started in Gibraltar.

#### A. Landing on the islands broke

Only the coastal cut changed island cells since m3b, so it is the cause. Hypotheses, most
likely first:

1. **The cell header holds record indices.** `cut_footprint` inserts the type-7 half right
   after the land record, shifting every later record's index by one, and keeps the old
   8-byte header. Coastal headers carry small values (0x01, 0x03, 0x13, 0x14, ...) next to
   0x1f/0x57/0xff, while interior cells carry only terrain-like patterns: those small values
   are probably per-direction pointers to the record a landing/coast transition uses.
   *Experiment:* in vanilla coastal cells, check whether each small header byte is a valid
   record index and which record type/edge it points at (8 bytes = 8 neighbour directions,
   like `passable_part`?). *Cheap fix to try first:* append the type-7 records at the END of
   the cell's record list (no index shift), recompute fields, rebuild.
2. **Sea-to-land connectivity is encoded somewhere we rewrote.** The sea class ranks
   exclude land, so `unknown2` never links sea and land polygons; landing must use another
   link (header, a type-2 coast record, or shared vertices). *Experiment:* diff the edited
   cells and their neighbours before/after the cut (every record's a/b, entries and the
   header) and list anything besides the intended records that changed.
3. **The new land polygon lost its coastline vertices' identity** (re-encoded vertex ids).
   *Check:* after the cut, every coastline vertex of the land record must keep its old
   vertex index; `encode` only reuses ids from the host's own entries, so confirm none were
   re-added.

*Test:* land an army on Tenerife, Gran Canaria and Fuerteventura (each has one edited cell
pair) and on an unedited island cell if possible; re-embark.

#### B. Gibraltar and its neighbouring ports

Not yet known when this started. Bisect with builds that already exist (no rebuild needed):

| build | contains | tells us |
|---|---|---|
| `canaries_m2b` (M2 only, on the 8-region build) | path-id renumber + startpos obstacle shift | if broken here, the renumber/obstacle shift is the cause |
| `bisect_dormant206_unlocked` | clean pipeline, Canaries dormant | same, on the from-vanilla pipeline |
| `new_regions_unlocked` | no 206th region at all | control: should be fine |

*Test in each:* march an army from Spain into Gibraltar, and into the two ports next to it;
sail a fleet through the strait.

Prime suspect: **the OBSTACLE_BOUNDARIES shift** in `relabel_pathfinding`. Gibraltar is a
fort-obstacle cell. The shift treated every record id in 85..1021 as a path id, but
obstacle entries may use part of that range for something else (e.g. type-7 fort/slot
records with their own ids). *Experiments:* (1) histogram (type, id) of OBSTACLE_BOUNDARIES
records in vanilla vs the same cells in pathfinding.esf, and confirm each obstacle record's
id matches the pathfinding record it shadows; (2) compare Gibraltar's obstacle entries in
vanilla, m2b and m4 record by record. *Fix if confirmed:* shift an obstacle record's id
only where the matching pathfinding record (same cell, same vertex list) was shifted.
Second suspect: the 9,817 renumbered startpos node sequence ids in grid 2 (the coastal cuts
add records); *check* the Gibraltar cells' OBSTACLE_BASE_GRID_NODE ids against
`sequence_index()` of the m4 grid.

#### C. After both are fixed

* Re-run the full test list: land/embark on all three islands, garrison in and out of La
  Laguna, Las Palmas and Fuerteventura Grain, Gibraltar by land and sea, several end turns,
  Banjar and the American regions unchanged.
* Commit `coastal.py`, the `reactivate_region.py` coastal/vertical-edge changes and these
  notes (the coastal work is uncommitted; last commit d8dd93c).
* Optional, later: the ~15 per-manager AI beliefs still empty on the new region (9.5),
  Lanzarote (no room for a slot outline), the preopen region counts / ownership map.

### 9.10 The path id -> region map, and the M2 order-list bug (2026-10-05)

**The map is `i2[order[pid] - 1]`, not `i2[pid]`.** Checking every path id's cells against the
regions.esf outlines: `i2[p]` names the right region for 62 of 83 tested grid-2 pids,
`i2[order[p] - 1]` for 80, where `order` = the first n entries of `grid_data[11]` (the 3 left
are small islands whose sampled cell centres fall inside a sea region's outline; both readings
agree there). The 18 it fixes are vanilla's swaps: palestine/tripoli, the
gibraltar/wilderness_khiva/spain rotation (Spain's cells are pid 19, Gibraltar's 17, Khiva's
18), astrakhan/moldavia, bohemia/ukraine, belarus/poland, silesia/saxony and a 5-cycle
bashkira -> ireland -> netherlands -> west_prussia -> muscovy. Grid 1 behaves the same.
`grid_data[11]` is therefore `order` (per pid, its 1-based i2 slot) followed by the border
groups `[count, 1-based i2 slots ...]`; the first group is empty (the sea id n), and the group
`[19, 20]` is spain-gibraltar (read as pids it would be khiva-spain). Corrections: 8.12's
"Khiva cells carry spain's id" was this misreading, since Khiva's cells carry Khiva's own pid,
and the 24 "cell labelled elsewhere" geometry warnings disappear with the right map. 9.4's
"order roughly by southern edge" was wrong too.

**The bug.** M2 inserted the new pid's order entry at position 8 ("by southern edge"), so
every later pid took its predecessor's slot: in `canaries_m2` .. `canaries_m4` 78 of 86 pids
mapped to the wrong region. Among them: pid 19 (Spain's land) -> wilderness_khiva, 17
(Gibraltar) -> malta, 22 (Portugal) -> morea, 85 (the islands) -> iceland and 8 (Tunis) ->
canary_islands. That fits "armies cannot enter Gibraltar or the ports next to it unless
they started there" (9.9 B) far better than geometry. In the passability graph that the
fields encode (9.12), Spain -> Gibraltar's settlement outline is reachable in every build,
and pathfinding.esf around Gibraltar is identical to vanilla modulo the id shift. Fix:
`relabel_pathfinding` appends `n + 1` to `order`. `AreaGrid.region_of` / `pid_of` hold the
map, and `add_region.py` and `check_geometry` now use it.

Also measured while ruling out the 9.9 B suspects:

* Every OBSTACLE_BOUNDARIES record's path id is a path id of its own pathfinding cell
  (62,889 of 62,889 in vanilla and in `canaries_m5`), so shifting them in M2b was right.
  Obstacle-only record types 8-11 exist (no pathfinding.esf record has them).
* No fort obstacle sits at Gibraltar. The obstacle entries there belong to CHARACTER_OBSTACLEs.
* startpos `PATHFINDING_GRID` item [8] (268 bools = ids 0..267) is indexed by pid: True on
  all eight dormant regions (Khiva via pid 18), a few desert/sea regions, the sea id and two
  border groups. Meaning unknown. The new pid copies unexplorable's True.

### 9.11 What a vanilla footprint is; coastal footprints v2

**Rule (every vanilla case):** a settlement's pathfinding slot outline (type 7) is exactly its
regions.esf `settlement_and_slots[1]` polygon (137 of 137 within 0.01 sq units: a diamond of
area 5.761 for minor settlements, 17.0 for major capitals), and a town / resource / port
slot's is exactly its `slot[6]` polygon (725 of 725; a port's `[8]` is a larger 6.9 zone that
is not in pathfinding). The outline replaces every land and impassable record under it, and
**none of the 862 borders a sea record**. Cell invariants: 76,691 of 76,692 vertices on a cell
edge are also vertices of the neighbour's records (the exception is at the map border,
r187 c179), and inside a cell, records meeting along an edge share its vertices (118
near-collinear exceptions in 14,719 cells).

`canaries_m4` broke all of these. Its 0.9 octagons covered only 11-24% of the regions.esf
polygons the game also reads, and La Laguna's octagon sat 0.3 from a third cell edge, so it
was clipped there, leaving two T-junction vertices. The rank shift in neighbouring sea
records (a slot inserted before a sea record moves the sea's rank) is vanilla behaviour, not
a bug: type 7 counts in the sea class for 546 of 609 vanilla neighbour links.

`coastal.py` v2 replaces the octagon cutter:

* `find_outline_site`: the outline is the template's own polygon, translated exactly as
  `patch_regions_esf` writes it (`template_outline` + `translate_footprint`), at the nearest
  0.05-lattice point to the desired position that is inside the region, at least 0.15 from
  every sea record, at least `min_land` on the region's land (capital 0.7, slots 0.5; the
  rest impassable), over header cells only, clear of other land, slot outlines and obstacle
  cells (and their 4-neighbours), with no vertex on a cell edge.
* `plan_carve`: per cell, the outline is cut out of the land / impassable records under it.
  It refuses holes, slivers, sea or slot contact, and a split-off land piece that does not
  border the outline.
* `carve_footprint`: appends the per-cell pieces at the end of each cell's record list. It
  keeps every untouched record's vertex list and nodes each new ring at every point where
  another record meets it (the first m5 attempt missed this and left a 0.2 sq-unit land
  piece with no links on Fuerteventura). It then recomputes pass / unknown2 for the cells
  and their 4-neighbours.
* After each carve `place_coastal_footprints` asserts `check_cells`, `check_nodes` and that no
  land record is left with pass 0 / unknown2 0.

Transplanted land footprints (Pensa) keep their template's links to the neighbouring cells.
Two blocks have a header-cell neighbour where Pensa's was interior (Khiva's Astrabad r61
c284, grid 1 r140 c44), so `refresh_fields` now recomputes the transplanted records. Every
grid then reproduces exactly vanilla's field-rule misses (grid 0/1/2: 0/490/232, the same
records).

### 9.12 `canaries_m5`: build, static checks, test plan (2026-10-05)

```bash
# Git Bash, game-dir clone (setup lines as in 8.21)
$PY "$REPO/scripts/add_region.py" --regions-esf data/gc/regions.esf \
    --startpos-esf data/campaigns/main/startpos_vanilla.esf \
    --pathfinding-esf data/gc/pathfinding.esf --out out/canaries_base5
$PY "$REPO/scripts/reactivate_region.py" --regions-esf out/canaries_base5/regions.esf \
    --pathfinding-esf out/canaries_base5/pathfinding.esf \
    --startpos-esf out/canaries_base5/startpos.esf --footprints --obstacles none --out out/canaries_m5
$PY "$REPO/scripts/unlock_factions.py" --src out/canaries_m5/startpos.esf \
    --dest out/canaries_m5_unlocked/startpos.esf
cp out/canaries_m5/{regions.esf,pathfinding.esf,new_regions.pack,MANIFEST.txt} out/canaries_m5_unlocked/
```

Sites: La Laguna (-116.70, 201.68) on west-central Tenerife (the full diamond needs 1.4 units
of room to the SW of the requested NE point), Las Palmas (-109.91, 199.57), Fuerteventura
Grain (-99.58, 201.79).

Static checks of `canaries_m5_unlocked` (all against vanilla):

| check | result |
|---|---|
| pid -> region map, grid 2 | 80/84 like vanilla's 80/83 (old base: 8/84) |
| outline == regions.esf polygon, Canaries | 3 of 3 exact, no sea contact |
| cell invariants (partition, ccw, edge vertices) | same as vanilla (only r187 c179) |
| in-cell T-junctions near the Canaries | 0 (m4: 0, first m5 attempt: 10) |
| field-rule misses, grids 0/1/2 | 0/490/232, identical record sets to vanilla |
| island components in the encoded graph | 4 = the four islands with land; 3 hold a slot |
| sea around the islands | one component (477 nodes) |
| Spain -> Gibraltar settlement outline | reachable |
| obstacle invariants, all 7 grids | 0 errors; no edited cell carries an obstacle node |
| obstacle record ids vs cell path ids | 62,889 / 62,889 |
| `verify_region_mod.py`, all nine regions | ALL PASS bar the known unplaced Sumba and Mistassini slots (as trade2) |

Bisect build `canaries_m5nofp_unlocked` = the same with `--no-footprint canary_islands` (the
map fix without any Canaries footprint, i.e. m3b + fix).

**Test plan.** Start a new Spain campaign on `canaries_m5_unlocked`:

1. Gibraltar (9.9 B): march an army from Spain into Gibraltar, and to Cadiz and Gibraltar's
   port; sail a fleet through the strait. Also walk Madrid -> Lisbon (Portugal's cells were
   mapped to Morea before).
2. Landing (9.9 A): land an army on Tenerife away from La Laguna and straight into La Laguna,
   and on Gran Canaria, Fuerteventura and Lanzarote. Re-embark each time.
3. La Laguna garrison: recruit there, leave and re-enter; Las Palmas and Fuerteventura
   Grain slots reachable.
4. Several end turns; Banjar, Arabia, Khiva and the American regions unchanged.

If 1 passes and 2 fails, test `canaries_m5nofp_unlocked`: landing works there -> the carve is
still at fault; it fails there too -> the cause is elsewhere (it worked in m3b, which had the
same footprint-less islands but the broken map).

## 10. 2026-10-05: splitting a mainland region (France batch)

The Canaries worked in game (all tests pass; fog of war deferred). Next phase (memory
etw-split-decisions): openETW-scale splits through a general tool, starting with France.
Batch 1, approved on its review map (France, Occitania, Brittany, Normandy, Burgundy with
Franche-Comté, Lyonnais with Dauphiné, Provence, plus Andorra from Spain). Occitania is built
and tested alone first. A mainland split differs from the Canaries in three derived layers
the Canaries never touched: cutting one area in `regions.esf` (mesh, outlines, quadtree), a
new land border in pathfinding (border group id), and AI adjacency between parent and child.

### 10.1 Borders on a distorted map

The campaign map is not a projection of real geography. A quadratic fit lon/lat -> map
coordinates over France's 13 named cities is good to 1.5 map units (rms) in the south,
but the north-west is off by several units (the Cotentin, the Seine inlet that runs to
x 13.5, the Loire inlet), so those borders are traced in map coordinates. Region pieces are
assigned in order (Occitania by a ~1700 polyline, then polygons), France keeps the rest, and
stray crumbs below 3 units² (river-inlet slivers) merge into the neighbour they border most.

### 10.2 `regions.esf` area records

Region record: `[name, type (land/sea/river), bbox min, bbox max, areas, theatre flag,
settlement_and_slots]`. Area record (10 fields): `[0] [1]` False, `[2]` True on the main
area only, `[3] [4]` bbox, `[5]` a class id (not unique: 13 on France's mainland and 94 other
areas; 0..856 all used), `[6] faces` = a triangle list over the vertex table that groups of
areas share verbatim (France: areas 0-7 one 1,751-triangle list, 8-11 another),
`[7] outlines` = closed rings (coast/borders) plus open polylines (rivers, `[0]` False),
`[8]` 65535 on the main area, ~190 on mountain sub-areas, `[9]` 104 on all of France's.

Outline `connectivity` = run-length list along the ring: `[neighbour region<<16|area,
first vertex, last vertex]` per stretch (France's coast ring: 34 runs: channel, Biscay,
Spain, each Pyrenees sub-area, Mediterranean, Savoy, Alsace, Flanders, river region `all`).

### 10.3 The `query_info` quadtree, decoded exactly

3,685 nodes, 2,764 leaves; every node stores its own `[lo, hi]`, children in the order
bottom_left, top_left, bottom_right, top_right; a leaf is a node `[lo, hi, cell]` with
`cell = [point, default region<<16|area, segments (v1, v2, tagA, tagB) * n]`.

* **Default** = the region/area containing the cell's stored point: 2,764 of 2,764. The point
  is the leaf's bottom-left corner, except in 149 leaves where the builder moved it to a fixed
  fraction of the box (136 at mid-height on the left edge), presumably off an edge.
* **Membership** = every outline edge (undirected) whose overlap with the leaf box has
  positive length: 0 missed, 0 extra over all leaves. Touching at a point does not count.
* **Sides**: for a stored `(a, b, A, B)`, B is the area whose outline runs a->b and A the area
  whose outline runs b->a (47,087 of 47,087 edges with both owners). An edge may be stored in
  opposite directions in neighbouring leaves (1,541 are); the reversed copy swaps the tags.
* Only 184 outline edges are stored nowhere: 20-unit edges of sea-region outlines.

So a split can update the tree locally and exactly: add the new border edges to every leaf
they overlap, split the coast / river edges the border crosses, re-tag edges by which area's
outline runs each way, and recompute defaults from the stored points.
