# Phase 0 Findings

**Status:** COMPLETE — all critical questions answered

## File Inventory

| File | Location | Size | Notes |
|------|----------|------|-------|
| pathfinding.esf (GC) | data/gc/ | 7.5 MB | Extracted from data/campaign_maps/global_map/ |
| regions.esf (GC)     | data/gc/ | 14 MB  | Extracted from data/campaign_maps/global_map/ |
| europe_lookup.tga    | data/gc/ | 145 KB | Region colour lookup map |
| europe_map.tga       | data/gc/ | 431 KB | Terrain map |
| pathfinding.esf (WP) | data/warpath/ | 2.4 MB | Warpath = natives_map |
| regions.esf (WP)     | data/warpath/ | 6.7 MB | |

Note: Files live on disk at data/campaign_maps/ — no pack extraction needed.
The Lord_Core.pack also contains pathfinding.esf / regions.esf (7.8 MB version — slightly different, likely unpatched).

## CRITICAL: West Pommerania Status

**In regions.esf:** YES — `west_pommerania=94.76274,374.42502` (world coordinate outline centre)
**In pathfinding.esf:** YES — cell_id=107, list index 65 in European grid

**Conclusion:**
- [x] **Method A available (reactivation only)** — west_pommerania grid cells already exist in pathfinding.esf. The region is dormant, not missing. We only need to activate it in startpos.esf.

This means we do NOT need the full rasterizer for west_pommerania — it is the lowest-hanging fruit.
**Start ETWPC-27 (reactivation path) immediately.**

## Pathfinding.esf Top-Level Structure

```xml
<esf magic="43982 0 1243518333">
 <node_types>
  <node_type name="root"/>
  <node_type name="pathfinding_areas"/>
  <node_type name="vertices"/>
  <node_type name="grid_data"/>
  <node_type name="grid_cells"/>
  <node_type name="boundaries"/>
 </node_types>
 <rec type="root">
  <ary type="pathfinding_areas">
   <xml_include path="pathfinding_areas-0001.xml"/>  ← east_indies (800,−80) 60×38
   <xml_include path="pathfinding_areas-0002.xml"/>  ← americas (−760,0) 210×230
   <xml_include path="pathfinding_areas-0003.xml"/>  ← europe (−180,140) 310×190  ← TARGET
   <xml_include path="pathfinding_areas-0004.xml"/>  ← india (440,20) 120×130
   <xml_include path="pathfinding_areas-0005.xml"/>  ← unknown (265,−140) 43×43
   <xml_include path="pathfinding_areas-0006.xml"/>  ← unknown (−350,−230) 50×45
   <xml_include path="pathfinding_areas-0007.xml"/>  ← unknown (−10,−40) 50×45
  </ary>
 </rec>
```

Each pathfinding_areas record contains:
- `<vertices>` — vertex coordinate list (world space floats, referenced by boundary records)
- `<grid_data>` — the occupancy grid (our primary target)

## European Grid Constants (ETWPC-6 — RESOLVED)

From `grid_data-0003.xml`:

| Constant | Value | Notes |
|----------|-------|-------|
| Grid origin (world) | (−180.0, 140.0) | Top-left corner of grid |
| Cell size | **2.0 world units** | Confirmed: 620/310 = 380/190 = 2.0 |
| Grid dimensions | 310 cols × 190 rows | |
| Bounding box (world) | (−180, 140) → (440, 520) | |
| Starting X Cell (global) | 550 | Global grid offset |
| Starting Y Cell (global) | 390 | Global grid offset |

**Transform: world → grid cell**
```python
col = int((world_x - (-180.0)) / 2.0)  # = int((world_x + 180.0) / 2.0)
row = int((world_y - 140.0) / 2.0)
```

**Verification with west_pommerania** (world pos 94.76274, 374.42502):
- col = int((94.76 + 180.0) / 2.0) = int(137.38) = 137
- row = int((374.43 - 140.0) / 2.0) = int(117.21) = 117

## Region List — European Grid (i2_ary from grid_data-0003)

85 passable regions. Key ones (cell_id → name):
```
107 → west_pommerania   (list index 65, world centre 94.76, 374.43)
105 → hannover          (list index 64)
 97 → saxony            (list index 63)
 98 → silesia           (list index 62)
108 → west_prussia      (list index 66)
110 → prussia           (list index 69)
115 → denmark           (list index 70)
```

Full list order (cell_id, name):
35=egypt, 37=morocco, 43=baluchistan, 41=wilderness_arabia, 42=persia,
40=palestine, 39=tripoli, 3=unexplorable, 44=tunis, 45=mesopotamia,
46=afghanistan, 49=syria, 47=algiers, 55=azerbaijan, 53=greece,
54=anatolia, 51=malta, 56=wilderness_khiva, 50=spain, 128=gibraltar,
59=naples, 52=morea, 57=portugal, 48=mediterranean_sea, 60=armenia,
58=sardinia, 65=rumelia, 67=georgia, 63=the_papal_states, 62=corsica,
76=bosnia, 68=chechenya-dagestan, 78=bulgaria, 70=france, 77=serbia,
80=don_voisko, 71=savoy, 72=genoa, 75=croatia, 82=milan, 79=crimea,
83=venice, 73=austria, 64=adriatic_sea, 90=astrakhan, 88=moldavia,
87=transylvania, 86=hungary, 89=tatariya, 93=alsace, 85=wurttemberg,
81=alps, 84=bavaria, 99=galicia, 101=ukraine, 96=bohemia, 95=flanders,
94=rhineland, 100=belarus, 109=poland, 91=england, 98=silesia,
97=saxony, 105=hannover, 107=west_pommerania, 111=lithuania,
113=bashkira, 103=ireland, 106=netherlands, 108=west_prussia,
112=muscovy, 110=prussia, 115=denmark, 114=scotland, 117=sweden,
116=baltic_sea, 119=komi, 118=courland, 121=estonia_and_livonia,
123=arkhangelsk, 120=norway, 122=ingria, 124=finland, 125=karelia,
126=iceland

## Boundary Record Semantics (partial — ETWPC-8)

From boundary comments in XML, confirmed fields:
- `passable_part` — fraction of boundary that is passable (0–255, e.g. 255=fully passable)
- `path_type` — 0 = passable area, others TBD
- `path_id` — identifies the boundary path (annotated with adjacent region names)
- `vertex_index` — index into the vertices array
- Vertices are listed as world-space (x, y) float pairs in comment

## Other Dormant/Missing Regions to Check

From initial Pommerania search, also confirmed in pathfinding.esf:
- silesia (cell_id=98) — present
- hannover (cell_id=105) — present
- saxony (cell_id=97) — present

Need to check: jutland, zealand, alsace/lorraine split, brittany, normandy
→ run `python scripts/grep_regions.py out/gc_pathfinding/grid_data-0003.xml --search "jutland"`
