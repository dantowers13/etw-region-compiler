"""
Reactivate a dormant ETW region: give an existing-but-unsettled region a settlement.

Why this route
--------------
Eight regions in regions.esf already have a mesh, quadtree entries, pathfinding
cells, an AI region record and AI border links, but no settlement: the five
American wildernesses, wilderness_arabia, wilderness_khiva and unexplorable.
The Lord mod's "URR" sub-mod shipped exactly this activation for all eight and
it runs, so every structure touched below is copied from that precedent, but
built from vanilla files with a vanilla template region.

Nothing geometric is generated and **pathfinding.esf is not touched at all** --
URR, the shipped precedent, ships it byte-identical to vanilla. Navigability for
the new settlement comes from a fort obstacle in startpos instead: without one the
settlement is a "trap cell" that armies can enter and never leave (seen
2026-09-06; openETW describe the same vanilla defect at Montagnais/Labrador).
See compiler/obstacles.py.

What is written
---------------
regions.esf   regions[i].flag -1 -> theatre, + settlement_and_slots (cloned from
              the template region, positions moved), + theatre region_keys entry
startpos.esf  REGION (clone of template), owner's GOVERNORSHIP list, cloned
              CAMPAIGN_PATHFINDER fort obstacles (see add_fort_obstacles),
              CAI_WORLD_SETTLEMENTS, CAI_WORLD_BUILDING_SLOTS (one per slot),
              CAI_WORLD_REGION_SLOTS (resource/town slots), CAI_WORLD_REGIONS[i]
              links, CAMPAIGN_TRADE_MANAGER/SETTLEMENT_INDICES, and every pure
              id-list that referenced the template's region / recruitment ids
<name>.pack   db rows: regions, campaign_map_settlements, campaign_map_slots,
              campaign_map_towns_and_ports. Written as a PFH0 type-4 "movie" pack
              so it auto-loads. If the pack is NOT loaded the frontend crashes at
              Empire.exe+0x4e2165 on the Grand Campaign button: every region_keys
              name is looked up in db/regions_tables ("In table %S: '%S' is not a
              valid key") and the null REGION_RECORD's colour is then read.

Run from the clone that has the game data (defaults assume data/gc and
data/campaigns/main):

    python scripts/reactivate_region.py --region wilderness_arabia
    python scripts/reactivate_region.py --list

Outputs go to out/<region>/ ; deploy with scripts/deploy_mod.py.
"""

from __future__ import annotations

import argparse
import copy
import math
import struct
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from etwpc.io.esf_reader import ESFReader  # noqa: E402
from etwpc.io.esf_writer import ESFWriter  # noqa: E402
from etwpc.compiler.footprint import AreaGrid, find_clean_target  # noqa: E402
from etwpc.compiler.obstacles import (  # noqa: E402
    ObstacleCloner, ObstacleSystem, donor_obstacles_in, verify_obstacles,
)
from etwpc.io.esf_types import (  # noqa: E402
    ESFNode, ESFPrimitive,
    T_BOOL, T_BOOL_T, T_BOOL_F, T_I4, T_U4, T_V2, T_UNICODE, T_ASCII,
    T_U4_0, T_U4_1, T_U4_B, T_U4_W, T_U4_3, T_I4_0, T_I4_B, T_I4_W, T_I4_3,
    T_U4_ARY, T_U1_ARY, T_RECORD_ARY,
)

FIXED = 1 << 20


# ─── configuration ────────────────────────────────────────────────────────────

@dataclass
class SlotSpec:
    key: str            # e.g. "sheep:wilderness_arabia:sakaka"
    kind: str           # settlement_minor | settlement_fortification | settlement_road | resource | town
    pos: tuple[float, float]
    slot_type: str = ""          # DB slot type for resource ("sheep"), town type for town ("town", "town-metal")
    display: str = ""            # towns_and_ports display name
    src: str = ""                # a slot moved from the parent region: its parent key
    stay: bool = False           # ... that stays with the parent (only its position moves)


@dataclass
class RegionSpec:
    name: str
    theatre_flag: int                 # regions.esf flag = which theatre's region_keys lists it
    pf_grid: int                      # pathfinding area / startpos PATHFINDING_GRID index
    theatre_name: str                 # startpos REGION[22]
    template: str                     # vanilla region cloned for startpos/regions records;
                                      # must be owned by owner_faction (its governorship is reused)
    region_display: str               # regions_onscreen_<name>
    settlement_key: str
    settlement_display: str
    # donor (URR) slot key -> (our key, display name, DB slot/town type). Positions are
    # read from the donor regions.esf, which placed every one on a valid cell.
    slot_map: dict[str, tuple[str, str, str]]
    donor_settlement_key: str
    owner_faction: str                # faction key, e.g. "ottomans"
    emergent_nation: str
    rebels_name: str
    culture: str
    population: tuple[int, int, int]  # POPULATION[1..3]
    colour: tuple[int, int, int]      # DB regions RGB == the region's palette colour in <theatre>_lookup.tga
    religion: tuple[tuple[str, float], ...]
    region_factors_pop: tuple[int, int] | None = None   # REGION_FACTORS[2], [4]; None = population[0], [1]
    settlement_tier: int | None = None            # None = the template's
    resources: list[str] | None = None            # REGION[36]; None = the template's
    wealth: tuple[int, int] | None = None         # REGION[10], [11]; None = the template's
    wealth_distribution: tuple[int, ...] | None = None   # REGION[31]
    los_bbox: tuple[tuple[float, float], tuple[float, float]] | None = None   # None = outline bbox
    prestige: int = 10
    town_growth: int = -5
    # filled in by resolve_spec()
    capital: tuple[float, float] = (0.0, 0.0)
    slots: list[SlotSpec] = field(default_factory=list)
    continent: str = ""
    # Fort obstacles are cloned from a donor startpos that already has this region
    # working (the Lord mod's URR). The donor's pathfinding grid must be the vanilla
    # one, which URR's is, so every cell sequence index in it is still valid here.
    obstacle_donor: str = "../data/campaigns/Lord_main/startpos.esf"
    # Regions URR does not have (a new 206th region, add_region.py): explicit positions,
    # our slot key (or "capital") -> (x, y), replacing the donor lookup.
    positions: dict[str, tuple[float, float]] | None = None
    footprints: bool | str = True     # False: none; "coastal": regions.esf outlines carved into coastal cells (coastal.py)
    # A region split off a live one (split_region.py, deep_dive 10). The parent's slot
    # records MOVE here, keeping their ids, buildings and AI history (deleting one would
    # leave ~300 AI references dangling): parent slot key -> (our key, display, new position
    # or None to stay[, True = the slot stays with the parent and only moves]). Our key None
    # keeps the parent's key (ports: trade_routes.esf and sea_grids.esf name them by key).
    # capital_slot = a parent slot whose old spot becomes the capital. parent_share = this
    # region's share of the parent's (population, wealth). slot_buildings: new slot key ->
    # a vanilla slot whose starting building it copies (Mont-Saint-Michel's church school).
    parent: str | None = None
    transfers: dict[str, tuple] | None = None
    slot_buildings: dict[str, str] | None = None
    capital_slot: str | None = None
    parent_share: tuple[float, float] = (0.0, 0.0)
    moved: list[SlotSpec] = field(default_factory=list)      # filled in by resolve_spec()
    capital_raw: bytes | None = None    # the capital kept capital_slot's outline (place_split_footprints)


# Names are ~1700 (approved 2026-10-01); URR's legendary/anachronistic ones are replaced.
# Owners must own a land region in vanilla (rebels own none), and the template must be
# one of that owner's regions.
SPECS: dict[str, RegionSpec] = {s.name: s for s in [
    RegionSpec(
        name="wilderness_arabia", theatre_flag=2, pf_grid=2, theatre_name="europe", template="armenia",
        region_display="Jawf", settlement_key="settlement:wilderness_arabia:nefud", settlement_display="Tabuk",
        donor_settlement_key="settlement:wilderness_arabia:nefud",
        slot_map={
            "sheep:wilderness_arabia:sakaka": ("sheep:wilderness_arabia:sakaka", "Sakaka Flocks", "sheep"),
            "town:wilderness_arabia:arar": ("town:wilderness_arabia:dumat_al_jandal", "Dumat al-Jandal", "town"),
        },
        owner_faction="ottomans", emergent_nation="ottoman_rebels", rebels_name="Arabian Rebels",
        culture="sc_mideast_islamic", population=(300000, 375000, 300000), colour=(207, 190, 106),
        religion=(("rel_islamic", 0.9), ("rel_orthodox", 0.1)), region_factors_pop=(339458, 375000),
        resources=["desert", "med_coast", "camels", "north_africa", "africa_west_indies", "galleys",
                   "colonial_euro_factions", "global", "middle_east", "middle_east_and_europe"],
        wealth=(200, 200), wealth_distribution=(44, 132, 176, 22, 110, 110, 110, 66),
        los_bbox=((243.75, 173.75), (412.5, 238.75)),
    ),
    RegionSpec(
        name="wilderness_khiva", theatre_flag=2, pf_grid=2, theatre_name="europe", template="persia",
        region_display="Khwarezm", settlement_key="settlement:wilderness_khiva:khiva", settlement_display="Khiva",
        donor_settlement_key="settlement:wilderness_khiva:khiva", settlement_tier=2,
        slot_map={
            "town:wilderness_khiva:dashauz": ("town:wilderness_khiva:urgench", "Urgench", "town-textile"),
            "town:wilderness_khiva:ust_jick": ("town:wilderness_khiva:guryev", "Guryev", "town-metal"),
            "sheep:wilderness_khiva:nukus": ("sheep:wilderness_khiva:kungrad", "Kungrad Pastures", "sheep"),
            "egypt:wilderness_khiva:kejla": ("egypt:wilderness_khiva:nisa", "Nisa Cotton Fields", "egypt"),
            "gold:wilderness_khiva:busurulan": ("gold:wilderness_khiva:sarakhs", "Sarakhs Mines", "gold"),
            "rice:wilderness_khiva:astrabad": ("rice:wilderness_khiva:astrabad", "Astrabad Paddies", "rice"),
        },
        owner_faction="safavids", emergent_nation="persian_rebels", rebels_name="Khivan Rebels",
        culture="sc_mideast_islamic", population=(300000, 375000, 300000), colour=(209, 169, 73),
        religion=(("rel_islamic", 1.0),),
    ),
    RegionSpec(
        # The East Indies theatre has no vanilla land region; URR files this one under
        # the europe region_keys (flag 2) with its pathfinding in grid 0.
        name="unexplorable", theatre_flag=2, pf_grid=0, theatre_name="east_indies", template="ceylon",
        region_display="Banjar", settlement_key="settlement:unexplorable:banjarmasin", settlement_display="Banjarmasin",
        donor_settlement_key="settlement:unexplorable:banjarmasin",
        slot_map={
            "town:unexplorable:amuntay": ("town:unexplorable:amuntai", "Amuntai", "town-textile"),
            "town:unexplorable:palopo": ("town:unexplorable:palopo", "Palopo", "town-textile"),
            # a port in URR; a plain town here keeps fleets/harbours out of this build
            "port:unexplorable:pelaychari": ("town:unexplorable:tabanio", "Tabanio", "town-metal"),
            "tropical_humid:unexplorable:payti": ("tropical_humid:unexplorable:sumba", "Sumba Plantations", "tropical_humid"),
            "tropical_humid:unexplorable:kedari": ("tropical_humid:unexplorable:kendari", "Kendari Plantations", "tropical_humid"),
            "timber:unexplorable:ende": ("timber:unexplorable:flores", "Flores Forests", "timber"),
            "gems:unexplorable:kupang": ("gems:unexplorable:kupang", "Kupang Mines", "gems"),
        },
        owner_faction="netherlands", emergent_nation="maratha_rebels", rebels_name="Banjarese Rebels",
        culture="sc_indian_hindu", population=(200000, 250000, 200000), colour=(227, 172, 172),
        religion=(("rel_islamic", 0.8), ("rel_hindu", 0.2)),
    ),
    RegionSpec(
        name="wilderness_great_plains", theatre_flag=1, pf_grid=1, theatre_name="america", template="great_plains",
        region_display="Sioux Lands", settlement_key="settlement:wilderness_great_plains:blood_run",
        settlement_display="Blood Run", donor_settlement_key="settlement:wilderness_great_plains:sioux_falls",
        slot_map={
            "town:wilderness_great_plains:omaha": ("town:wilderness_great_plains:oto_village", "Oto Village", "town-metal"),
            "town:wilderness_great_plains:kansas": ("town:wilderness_great_plains:kanza_village", "Kanza Village", "town-textile"),
            "town:wilderness_great_plains:cedar_rapids": ("town:wilderness_great_plains:ioway_village", "Ioway Village", "town-textile"),
            "town:wilderness_great_plains:onigamiinsing": ("town:wilderness_great_plains:izatys", "Izatys", "town-metal"),
            "corn:wilderness_great_plains:one": ("corn:wilderness_great_plains:smoky_hill", "Smoky Hill Fields", "corn"),
            "corn:wilderness_great_plains:main": ("corn:wilderness_great_plains:des_moines", "Des Moines River Fields", "corn"),
            "gold:wilderness_great_plains:rockies": ("gold:wilderness_great_plains:front_range", "Front Range Gold", "gold"),
        },
        owner_faction="plains", emergent_nation="plains", rebels_name="Dakota Rebels",
        culture="sc_tribal_american", population=(5000, 6250, 5000), colour=(221, 125, 0),
        religion=(("rel_animist", 1.0),),
    ),
    RegionSpec(
        name="wilderness_tejas", theatre_flag=1, pf_grid=1, theatre_name="america", template="tejas",
        region_display="Apacheria", settlement_key="settlement:wilderness_tejas:la_junta",
        settlement_display="La Junta de los Rios", donor_settlement_key="settlement:wilderness_tejas:quivira",
        slot_map={
            "town:wilderness_tejas:oklahoma": ("town:wilderness_tejas:taovaya", "Taovaya", "town-textile"),
            "town:wilderness_tejas:amarillo": ("town:wilderness_tejas:palo_duro", "Palo Duro", "town-textile"),
            "town:wilderness_tejas:cibola": ("town:wilderness_tejas:pecos", "Pecos", "town-textile"),
            "town:wilderness_tejas:eldorado": ("town:wilderness_tejas:jumano", "Jumano", "town-metal"),
            "corn:wilderness_tejas:main": ("corn:wilderness_tejas:red_river", "Red River Fields", "corn"),
            "corn:wilderness_tejas:mesa": ("corn:wilderness_tejas:cimarron", "Cimarron Fields", "corn"),
            "southern_usa:wilderness_tejas:main": ("southern_usa:wilderness_tejas:canadian_river", "Canadian River Plains", "southern_usa"),
            "southern_usa:wilderness_tejas:big_bend": ("southern_usa:wilderness_tejas:chisos", "Chisos Plains", "southern_usa"),
        },
        owner_faction="pueblo", emergent_nation="pueblo", rebels_name="Apache Rebels",
        culture="sc_tribal_american", population=(4000, 5000, 4000), colour=(46, 58, 80),
        religion=(("rel_animist", 0.9), ("rel_catholic", 0.1)),
    ),
    RegionSpec(
        name="wilderness_mexico", theatre_flag=1, pf_grid=1, theatre_name="america", template="new_mexico",
        region_display="Nueva Vizcaya", settlement_key="settlement:wilderness_mexico:chihuahua",
        settlement_display="Chihuahua", donor_settlement_key="settlement:wilderness_mexico:chihuahua",
        slot_map={
            "town:wilderness_mexico:coyame": ("town:wilderness_mexico:julimes", "Julimes", "town-textile"),
            "town:wilderness_mexico:mojada": ("town:wilderness_mexico:mapimi", "Mapimi", "town-metal"),
            "silver:wilderness_mexico:south": ("silver:wilderness_mexico:parral", "Parral Silver Mines", "silver"),
            "corn:wilderness_mexico:rio_grande": ("corn:wilderness_mexico:conchos", "Conchos Valley Fields", "corn"),
        },
        owner_faction="new_spain", emergent_nation="mexico", rebels_name="Tarahumara Rebels",
        culture="sc_tribal_american", population=(20000, 25000, 20000), colour=(44, 75, 63),
        religion=(("rel_animist", 0.6), ("rel_catholic", 0.4)),
    ),
    RegionSpec(
        # URR calls it hudsonsbay, vanilla loc "Wilderness (Manitoba)": it is Rainy Lake
        name="wilderness_hudsonsbay", theatre_flag=1, pf_grid=1, theatre_name="america", template="huron_territory",
        region_display="Rainy Lake", settlement_key="settlement:wilderness_hudsonsbay:lac_la_pluie",
        settlement_display="Lac la Pluie", donor_settlement_key="settlement:wilderness_hudsonsbay:hahabaha",
        slot_map={
            "town:wilderness_hudsonsbay:bebamash": ("town:wilderness_hudsonsbay:kaministiquia", "Kaministiquia", "town-textile"),
            "fur:wilderness_hudsonsbay:makovajn": ("fur:wilderness_hudsonsbay:severn", "Severn Trading Post", "fur"),
            "fur:wilderness_hudsonsbay:ginivens": ("fur:wilderness_hudsonsbay:lac_des_bois", "Lac des Bois Trappers", "fur"),
            "silver:wilderness_hudsonsbay:gabe_anakvad": ("silver:wilderness_hudsonsbay:lac_seul", "Lac Seul Silver", "silver"),
        },
        owner_faction="huron", emergent_nation="huron", rebels_name="Ojibwe Rebels",
        culture="sc_tribal_american", population=(3000, 3750, 3000), colour=(39, 25, 222),
        religion=(("rel_animist", 0.9), ("rel_catholic", 0.1)),
    ),
    RegionSpec(
        # URR calls it canada, vanilla loc "Wilderness (Labrador)": it is Ungava
        name="wilderness_canada", theatre_flag=1, pf_grid=1, theatre_name="america", template="labrador",
        region_display="Ungava", settlement_key="settlement:wilderness_canada:great_whale_river",
        settlement_display="Great Whale River", donor_settlement_key="settlement:wilderness_canada:asinivakamig",
        slot_map={
            "town:wilderness_canada:naganash": ("town:wilderness_canada:chicoutimi", "Chicoutimi", "town-metal"),
            "timber:wilderness_canada:zyngob": ("timber:wilderness_canada:nemiscau", "Nemiscau Forests", "timber"),
            "iron:wilderness_canada:miskomakva": ("iron:wilderness_canada:ashuanipi", "Ashuanipi Iron", "iron"),
            "fur:wilderness_canada:bindigegizig": ("fur:wilderness_canada:naskapi", "Naskapi Trappers", "fur"),
            "fur:wilderness_canada:makovajn": ("fur:wilderness_canada:mistassini", "Mistassini Post", "fur"),
        },
        owner_faction="inuit", emergent_nation="inuit", rebels_name="Naskapi Rebels",
        culture="sc_tribal_american", population=(1500, 1875, 1500), colour=(162, 61, 61),
        religion=(("rel_animist", 0.9), ("rel_catholic", 0.1)),
    ),
    RegionSpec(
        # The 206th region: unexplorable's Canary areas split off by add_region.py
        # (deep_dive 9). URR has no Canaries, so positions are explicit (inland points
        # >= 0.6 from the coast; area 0 Tenerife, 1 Gran Canaria, 3 Lanzarote). The
        # islands are all coastal pathfinding cells: no clean block for a footprint yet.
        name="canary_islands", theatre_flag=2, pf_grid=2, theatre_name="europe", template="naples",
        region_display="Canary Islands", settlement_key="settlement:canary_islands:la_laguna",
        settlement_display="La Laguna", donor_settlement_key="",
        slot_map={
            "town:canary_islands:las_palmas": ("town:canary_islands:las_palmas", "Las Palmas", "town"),
            # Fuerteventura, "the granary of the Canaries"; Lanzarote's pathfinding cells
            # have no room for even a small slot outline (deep_dive 9.8)
            "wheat:canary_islands:fuerteventura": ("wheat:canary_islands:fuerteventura", "Fuerteventura Grain", "wheat"),
        },
        positions={"capital": (-115.499, 202.380),
                   "town:canary_islands:las_palmas": (-109.858, 199.567),
                   "wheat:canary_islands:fuerteventura": (-99.58, 201.79)},
        footprints="coastal",
        owner_faction="spain", emergent_nation="spanish_rebels", rebels_name="Canarian Rebels",
        culture="sc_european_south", population=(100000, 125000, 100000), colour=(241, 196, 15),
        religion=(("rel_catholic", 1.0),),
    ),
    RegionSpec(
        # Split off France by split_region.py (deep_dive 10): Guyenne-et-Gascogne and
        # Languedoc. Template alsace (France's European minor settlement). Toulouse, seat of
        # the Parlement of Languedoc, is the capital; France's Toulouse town slot moves to
        # Carcassonne, and the Bordeaux town and wine slots move with their buildings.
        name="occitania", theatre_flag=2, pf_grid=2, theatre_name="europe", template="alsace",
        region_display="Occitania", settlement_key="settlement:occitania:toulouse",
        settlement_display="Toulouse", slot_map={}, donor_settlement_key="",
        parent="france", capital_slot="town:france:toulouse",
        transfers={
            "town:france:toulouse": ("town:occitania:carcassonne", "Carcassonne", (15.37, 308.67)),
            "town:france:bordeaux": ("town:occitania:bordeaux", "Bordeaux", None),
            "wine:france:bordeaux": ("wine:occitania:bordeaux", "Bordeaux Vineyards", None),
        },
        parent_share=(0.2, 0.15), positions={},
        footprints="carve",
        owner_faction="france", emergent_nation="french_rebels", rebels_name="Occitan Rebels",
        culture="sc_european_south", population=(0, 0, 0), colour=(196, 30, 58),
        religion=(("rel_catholic", 0.8), ("rel_protestant", 0.2)),
        resources=["europe", "displaced_scots", "global", "displaced_irish", "france", "lancers",
                   "middle_east_and_europe"],
    ),
    # Batch 1 (approved 2026-10-05, deep_dive 10): split off France / Spain by split_region.py.
    RegionSpec(
        name="brittany", theatre_flag=2, pf_grid=2, theatre_name="europe", template="alsace",
        region_display="Brittany", settlement_key="settlement:brittany:rennes", settlement_display="Rennes",
        slot_map={}, donor_settlement_key="", parent="france", positions={"capital": (-10.99, 342.82)},
        transfers={
            "port:france:brest": (None, None, None),
            "town:france:nantes": ("town:brittany:nantes", "Nantes", None),
            "sheep:france:bretagne": ("sheep:brittany:bretagne", "Brittany Farmland", None),
        },
        parent_share=(0.12, 0.12), footprints="carve",
        owner_faction="france", emergent_nation="french_rebels", rebels_name="Breton Rebels",
        culture="sc_european_west", population=(0, 0, 0), colour=(23, 89, 151),
        religion=(("rel_catholic", 1.0),), resources=["europe", "displaced_scots", "global", "displaced_irish", "france", "lancers",
                   "middle_east_and_europe"],
    ),
    RegionSpec(
        # Mont-Saint-Michel: a town that starts with a Church School (copied from Dijon's)
        name="normandy", theatre_flag=2, pf_grid=2, theatre_name="europe", template="alsace",
        region_display="Normandy", settlement_key="settlement:normandy:rouen", settlement_display="Rouen",
        slot_map={"town:normandy:mont_saint_michel": ("town:normandy:mont_saint_michel", "Mont-Saint-Michel", "town")},
        donor_settlement_key="", parent="france",
        positions={"capital": (7.94, 351.51), "town:normandy:mont_saint_michel": (-10.56, 345.58)},
        transfers={"port:france:le_havre": (None, None, None)},
        slot_buildings={"town:normandy:mont_saint_michel": "town:france:dijon"},
        parent_share=(0.12, 0.12), footprints="carve",
        owner_faction="france", emergent_nation="french_rebels", rebels_name="Norman Rebels",
        culture="sc_european_west", population=(0, 0, 0), colour=(189, 64, 33),
        religion=(("rel_catholic", 0.95), ("rel_protestant", 0.05)), resources=["europe", "displaced_scots", "global", "displaced_irish", "france", "lancers",
                   "middle_east_and_europe"],
    ),
    RegionSpec(
        name="provence", theatre_flag=2, pf_grid=2, theatre_name="europe", template="alsace",
        region_display="Provence", settlement_key="settlement:provence:aix", settlement_display="Aix-en-Provence",
        slot_map={}, donor_settlement_key="", parent="france", positions={"capital": (39.27, 309.86)},
        transfers={"port:france:marseille": (None, None, None)},
        parent_share=(0.08, 0.10), footprints="carve",
        owner_faction="france", emergent_nation="french_rebels", rebels_name="Provencal Rebels",
        culture="sc_european_south", population=(0, 0, 0), colour=(121, 51, 145),
        religion=(("rel_catholic", 1.0),), resources=["europe", "displaced_scots", "global", "displaced_irish", "france", "lancers",
                   "middle_east_and_europe"],
    ),
    RegionSpec(
        # Lyon becomes the capital; its town slot moves to Montbrison (Forez): the Rhone
        # valley (Valence) is all obstacle-reshaped cells, and this is the one clean run cell
        # (no settlement diamond fits anywhere in the Lyonnais' clean cells, deep_dive 10.10)
        name="lyonnais", theatre_flag=2, pf_grid=2, theatre_name="europe", template="alsace",
        region_display="Lyonnais", settlement_key="settlement:lyonnais:lyon", settlement_display="Lyon",
        slot_map={}, donor_settlement_key="", parent="france", capital_slot="town:france:lyons",
        transfers={"town:france:lyons": ("town:lyonnais:montbrison", "Montbrison", (29.0, 323.05))},
        parent_share=(0.10, 0.12), positions={}, footprints="carve",
        owner_faction="france", emergent_nation="french_rebels", rebels_name="Lyonnais Rebels",
        culture="sc_european_south", population=(0, 0, 0), colour=(226, 158, 11),
        religion=(("rel_catholic", 0.85), ("rel_protestant", 0.15)), resources=["europe", "displaced_scots", "global", "displaced_irish", "france", "lancers",
                   "middle_east_and_europe"],
    ),
    RegionSpec(
        # Dijon becomes the capital; its town slot (with its Church School) moves to Auxerre
        name="burgundy", theatre_flag=2, pf_grid=2, theatre_name="europe", template="alsace",
        region_display="Burgundy", settlement_key="settlement:burgundy:dijon", settlement_display="Dijon",
        slot_map={}, donor_settlement_key="", parent="france", capital_slot="town:france:dijon",
        transfers={"town:france:dijon": ("town:burgundy:auxerre", "Auxerre", (24.92, 340.12))},
        parent_share=(0.12, 0.12), positions={}, footprints="carve",
        owner_faction="france", emergent_nation="french_rebels", rebels_name="Burgundian Rebels",
        culture="sc_european_west", population=(0, 0, 0), colour=(128, 26, 64),
        religion=(("rel_catholic", 0.95), ("rel_protestant", 0.05)), resources=["europe", "displaced_scots", "global", "displaced_irish", "france", "lancers",
                   "middle_east_and_europe"],
    ),
    RegionSpec(
        # the Pyrenean valley; Spain's Andorra town slot moves south into Spain as Urgell
        name="andorra", theatre_flag=2, pf_grid=2, theatre_name="europe", template="naples",
        region_display="Andorra", settlement_key="settlement:andorra:andorra_la_vella",
        settlement_display="Andorra la Vella", slot_map={}, donor_settlement_key="", parent="spain",
        capital_slot="town:spain:andorra",
        transfers={"town:spain:andorra": ("town:spain:urgell", "Urgell", (6.75, 296.25), True)},
        parent_share=(0.005, 0.005), positions={}, footprints="carve",
        owner_faction="spain", emergent_nation="spanish_rebels", rebels_name="Andorran Rebels",
        culture="sc_european_south", population=(0, 0, 0), colour=(10, 126, 70),
        religion=(("rel_catholic", 1.0),),
    ),
]}


def resolve_spec(spec: RegionSpec, regions_root, donor_regions_root, tpl_db_continent: str) -> None:
    """Fill capital, slots (template settlement layout + donor positions) and continent."""
    def sas_by_name(root, name):
        regs = root.children[3].children[3]
        return sas_of(regs.children[[region_name(r) for r in regs.children].index(name)])

    if spec.parent is not None:
        psas = sas_by_name(regions_root, spec.parent)
        pslots = {sl[0].value: sl for sl in psas.children[2].children}
        spec.capital = tuple(pslots[spec.capital_slot][2].value) if spec.capital_slot else spec.positions["capital"]
        towns = {r[0]: r for r in read_db_rows(VANILLA_TOWNS_DB, 3, 0)}
        spec.moved = []
        for pkey, tr in spec.transfers.items():
            key, display, newpos = tr[:3]
            kind = slot_kind(pkey)
            stype = towns[pkey][1] if kind in ("town", "port") else pkey.split(":")[0]
            pos = tuple(newpos) if newpos else tuple(pslots[pkey][2].value)
            spec.moved.append(SlotSpec(key or pkey, kind, pos, slot_type=stype, display=display, src=pkey,
                                       stay=len(tr) > 3 and tr[3]))
        donor = {dkey: spec.positions[key] for dkey, (key, _, _) in spec.slot_map.items()}
    elif spec.positions is not None:
        spec.capital = spec.positions["capital"]
        donor = {dkey: spec.positions[key] for dkey, (key, _, _) in spec.slot_map.items()}
    else:
        dsas = sas_by_name(donor_regions_root, spec.name)
        spec.capital = tuple(dsas.children[0].value)
        donor = {sl[0].value: tuple(sl[2].value) for sl in dsas.children[2].children}
    missing = set(spec.slot_map) - set(donor)
    assert not missing, f"{spec.name}: donor has no slot(s) {sorted(missing)}"
    # settlement slots follow the TEMPLATE's layout (minor town vs major city), so
    # every kind the startpos/CAI cloning needs exists in the template
    tsas = sas_by_name(regions_root, spec.template)
    slots = [SlotSpec(f"{spec.settlement_key}:{k}", k, spec.capital)
             for k in (sl[0].value.split(":")[-1] for sl in tsas.children[2].children
                       if sl[0].value.startswith("settlement:"))]
    for dkey, (key, display, stype) in spec.slot_map.items():
        kind = "town" if key.startswith("town:") else "resource"
        slots.append(SlotSpec(key, kind, donor[dkey], slot_type=stype, display=display))
    spec.slots = slots
    spec.continent = tpl_db_continent


# ─── ESF helpers ──────────────────────────────────────────────────────────────

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
    out = []
    st = [nd]
    while st:
        x = st.pop()
        if isinstance(x, ESFNode):
            if x.tag == tag:
                out.append(x)
            st.extend(x.children)
        elif isinstance(x, list):
            st.extend(x)
    return out


def deep_copy(n):
    if isinstance(n, ESFPrimitive):
        return ESFPrimitive(type_tag=n.type_tag, value=copy.deepcopy(n.value), raw=bytes(n.raw) if n.raw else b"")
    if isinstance(n, ESFNode):
        m = ESFNode(tag=n.tag, type_tag=n.type_tag, version=n.version)
        m.children = [deep_copy(c) for c in n.children]
        return m
    if isinstance(n, list):
        return [deep_copy(c) for c in n]
    return copy.deepcopy(n)


U4_FAMILY = {T_U4, T_U4_0, T_U4_1, T_U4_B, T_U4_W, T_U4_3}
I4_FAMILY = {T_I4, T_I4_0, T_I4_B, T_I4_W, T_I4_3}


def set_int(p: ESFPrimitive, v: int) -> None:
    """Assign an integer, widening compact encodings so the writer emits the value."""
    if p.type_tag in U4_FAMILY:
        p.type_tag = T_U4
    elif p.type_tag in I4_FAMILY:
        p.type_tag = T_I4
    else:
        raise TypeError(f"set_int on tag {p.type_tag:#x}")
    p.value, p.raw = int(v), b""


def set_str(p: ESFPrimitive, s: str) -> None:
    assert p.type_tag in (T_UNICODE, T_ASCII), hex(p.type_tag)
    p.value, p.raw = s, b""


def set_v2(p: ESFPrimitive, xy) -> None:
    assert p.type_tag == T_V2
    p.value, p.raw = (float(xy[0]), float(xy[1])), b""


def set_list(p: ESFPrimitive, vals) -> None:
    assert 0x40 <= p.type_tag <= 0x5F, hex(p.type_tag)
    p.value, p.raw = list(vals), b""


def set_bool(p: ESFPrimitive, b: bool) -> None:
    if p.type_tag in (T_BOOL_T, T_BOOL_F):
        p.type_tag = T_BOOL
    p.value, p.raw = bool(b), b""


# URR's parking spot for synthetic fort objects (fixed-point, = (-342.47, -151.94),
# open South Atlantic): all 70 of its synthetic forts sit here
FORT_PARK = (-359108416, -159322832)


def fixed(x: float) -> int:
    return int(round(x * FIXED))


def all_ints(root) -> set[int]:
    seen = set()
    st = [root]
    while st:
        x = st.pop()
        if isinstance(x, ESFNode):
            st.extend(x.children)
        elif isinstance(x, list):
            st.extend(x)
        elif isinstance(x.value, int) and not isinstance(x.value, bool):
            seen.add(x.value)
        elif isinstance(x.value, (list, tuple)) and x.value and isinstance(x.value[0], int):
            seen.update(x.value)
    return seen


class IdPool:
    """Fresh ids that collide with nothing in the file. Two ranges, like URR:
    object ids high (0x3A......), CAI ids in the 91 000 000 band."""

    def __init__(self, used: set[int]):
        self.used = used
        self.next_obj = 0x3A000000
        self.next_cai = 91_000_000

    def obj(self) -> int:
        while self.next_obj in self.used:
            self.next_obj += 8
        v = self.next_obj
        self.next_obj += 8
        self.used.add(v)
        return v

    def cai(self) -> int:
        while self.next_cai in self.used:
            self.next_cai += 1
        v = self.next_cai
        self.next_cai += 1
        self.used.add(v)
        return v


# ─── regions.esf ──────────────────────────────────────────────────────────────

def slot_kind(key: str) -> str:
    """settlement:<r>:<s>:settlement_minor -> settlement_minor; town:.. -> town;
    port:.. -> port; anything else (sheep:.., iron:..) -> resource."""
    if key.startswith("settlement:"):
        return key.split(":")[-1]
    head = key.split(":")[0]
    return head if head in ("town", "port") else "resource"


def region_name(item) -> str:
    return next(c.value for c in item if isinstance(c, ESFPrimitive) and c.type_tag == T_UNICODE)


def sas_of(item):
    return next((c for c in item if isinstance(c, ESFNode) and c.tag == "settlement_and_slots"), None)


def translate_footprint(raw: bytes, old_pos, new_pos) -> bytes:
    n = len(raw) // 4
    f = list(struct.unpack(f"<{n}f", raw))
    dx, dy = new_pos[0] - old_pos[0], new_pos[1] - old_pos[1]
    for i in range(0, n, 2):
        f[i] += dx
        f[i + 1] += dy
    return struct.pack(f"<{n}f", *f)


def patch_regions_esf(root, spec: RegionSpec, cai_pos) -> None:
    regs = root.children[3].children[3]
    names = [region_name(it) for it in regs.children]
    tgt = regs.children[names.index(spec.name)]
    tpl = regs.children[names.index(spec.template)]
    assert sas_of(tgt) is None, f"{spec.name} already has settlement_and_slots"
    flag = next(c for c in tgt if isinstance(c, ESFPrimitive) and c.type_tag in I4_FAMILY)
    assert flag.value == -1, f"{spec.name} flag is {flag.value}, expected -1 (dormant)"
    set_int(flag, spec.theatre_flag)

    tsas = sas_of(tpl)
    sas = deep_copy(tsas)
    tcap = tsas.children[0].value
    set_v2(sas.children[0], spec.capital)
    sas.children[1].raw = spec.capital_raw or translate_footprint(tsas.children[1].raw, tcap, spec.capital)
    sas.children[1].value = None

    by_kind = {}
    for sl in tsas.children[2].children:
        kind = slot_kind(sl[0].value)
        if kind != "port":
            by_kind.setdefault(kind, sl)
    new_slots = []
    for s in spec.slots:
        t = by_kind[s.kind]
        sl = deep_copy(t)
        set_str(sl[0], s.key)
        if s.kind in ("resource", "town"):
            set_str(sl[1], s.slot_type)
        old = t[2].value
        set_v2(sl[2], s.pos)
        set_v2(sl[3], s.pos)
        for k in (6, 8):
            if sl[k].raw:
                sl[k].raw = translate_footprint(t[k].raw, old, s.pos)
        new_slots.append(sl)
    if spec.parent is not None:
        # the parent's moved slots: re-keyed, relocated with their own outline polygons
        psas = sas_of(regs.children[names.index(spec.parent)])
        moved = {sl.src: sl for sl in spec.moved}
        keep = []
        for sl in psas.children[2].children:
            m = moved.get(sl[0].value)
            if m is None:
                keep.append(sl)
                continue
            old = tuple(sl[2].value)
            set_str(sl[0], m.key)
            if m.stay:
                keep.append(sl)
            if tuple(m.pos) != old:
                dx, dy = m.pos[0] - old[0], m.pos[1] - old[1]
                for k in (6, 8):
                    if sl[k].raw:
                        sl[k].raw = translate_footprint(sl[k].raw, old, m.pos)
                        sl[k].value = None
                set_v2(sl[2], m.pos)
                set_v2(sl[3], (sl[3].value[0] + dx, sl[3].value[1] + dy))
            if not m.stay:
                new_slots.append(sl)
        n_stay = sum(1 for m in moved.values() if m.stay)
        assert len(keep) == len(psas.children[2].children) - len(moved) + n_stay, "a moved slot is missing from the parent"
        psas.children[2].children = keep
    sas.children[2].children = new_slots
    tgt.append(sas)

    theatre = root.children[0].children[spec.theatre_flag][0]
    rk = theatre.children[4]
    assert spec.name not in [it[0].value for it in rk.children]
    proto = deep_copy(rk.children[0])
    set_str(proto[0], spec.name)
    set_v2(proto[1], cai_pos)
    rk.children.append(proto)
    print(f"regions.esf: {spec.name} flag -> {spec.theatre_flag}, {len(new_slots)} slots, region_keys now {len(rk.children)}")


def check_geometry(regions_root, pf_root, spec: RegionSpec) -> None:
    """Every configured position must be inside the region outline and on one of
    its pathfinding cells (interior run) or at least not in another region's run."""
    rd = regions_root.children[3]
    vraw = rd.children[0].children[0].raw
    xy = struct.unpack(f"<{len(vraw) // 4}f", vraw)
    regs = rd.children[3]
    names = [region_name(it) for it in regs.children]
    idx = names.index(spec.name)
    it = regs.children[idx]
    areas = next(c for c in it if isinstance(c, ESFNode) and c.tag == "areas")
    rings = []
    for a in areas.children:
        for ol in a[7].children:
            if ol[0].value:
                rings.append([(xy[2 * v], xy[2 * v + 1]) for v in ol[3].value])

    def inside(px, py):
        for ring in rings:
            c = False
            n = len(ring)
            for i in range(n):
                x1, y1 = ring[i]
                x2, y2 = ring[(i + 1) % n]
                if (y1 > py) != (y2 > py) and px < x1 + (py - y1) * (x2 - x1) / (y2 - y1):
                    c = not c
            if c:
                return True
        return False

    area = pf_root.children[0].children[spec.pf_grid]
    gd = area[2]
    ox, oy, cs = gd.children[0].value / FIXED, gd.children[1].value / FIXED, gd.children[4].value / FIXED
    cols = gd.children[5].value
    i2 = list(gd.children[10].value)
    order = list(gd.children[11].value)[:len(i2)]
    pid = order.index(i2.index(idx) + 1)          # AreaGrid.pid_of (deep_dive 9.10)
    owner = {}
    pos = 0
    for item in gd.children[12].children:
        blobs = [c for c in item if isinstance(c, ESFPrimitive) and c.type_tag == T_U1_ARY]
        n = 1 + (len(blobs[1].raw) // 12 if len(blobs) > 1 else 0)
        u2 = next((c.value for c in item if isinstance(c, ESFPrimitive) and c.type_tag == 0x07), None)
        owner[pos] = "boundary-cell"
        for j in range(1, n):
            owner[pos + j] = u2
        pos += n
    for label, (x, y) in [("capital", spec.capital)] + [(s.key, s.pos) for s in spec.slots + spec.moved if not s.stay]:
        cell = int((y - oy) / cs) * cols + int((x - ox) / cs)
        o = owner[cell]
        on_cells = o == pid or o == "boundary-cell"
        # being inside the outline is the hard requirement; a cell of another label
        # (a slot near a border) only warns
        status = "OK" if inside(x, y) and on_cells else ("WARN cell labelled elsewhere" if inside(x, y) else "BAD")
        print(f"  geometry {label:52} inside={inside(x, y)!s:5} cell-owner={o!s:14} {status}")
        assert inside(x, y), f"{label} at {(x, y)} is outside {spec.name}"


# ─── startpos.esf ─────────────────────────────────────────────────────────────

def sp_region_name(item) -> str:
    return next(c.value for c in find(item, "REGION").children if isinstance(c, ESFPrimitive) and c.type_tag == T_UNICODE)


def faction_id(root, key) -> int:
    """A FACTION's id is the u32 just before its key string. The key's index varies
    (ottomans: id [8] key [9]; most minors: id [7] key [8])."""
    for it in find(root, "FACTION_ARRAY").children:
        f = it[0]
        for i, c in enumerate(f.children[:14]):
            if isinstance(c, ESFPrimitive) and c.value == key and i > 0:
                return f.children[i - 1].value
    raise KeyError(key)


def clear_bdi(item) -> None:
    """Empty the belief/desire id caches of a cloned CAI component wrapper.

    Wrapper layout (settlement / region-slot: lists at 10,11 counts at 12,13;
    building-slot: lists at 8,9 counts at 10,11; then more lists further on).
    The cached ids belong to the template's desire objects, which must not be
    shared. Whether the engine tolerates empty caches is the first thing a
    crash dump would tell us; URR shipped populated ones."""
    list_idx = [i for i, c in enumerate(item) if isinstance(c, ESFPrimitive) and 0x40 <= c.type_tag <= 0x5F]
    for i in list_idx:
        set_list(item[i], [])
    first = list_idx[0]
    for i in (first + 2, first + 3):
        if isinstance(item[i], ESFPrimitive) and item[i].type_tag in U4_FAMILY:
            set_int(item[i], 0)


def patch_startpos(root, spec: RegionSpec, cai_region_idx: int, cai_region_ai_id: int, cai_pos,
                   ids: IdPool, los_bbox):
    ra = find(root, "REGIONS_ARRAY")
    names = [sp_region_name(it) for it in ra.children]
    assert spec.name not in names
    T = find(ra.children[names.index(spec.template)], "REGION")
    owner_id = faction_id(root, spec.owner_faction)
    assert T.children[19].value == owner_id, \
        f"template {spec.template} must be owned by {spec.owner_faction} (its governorship is reused)"
    theatre_id = list(find(find(root, "CAI_WORLD_REGIONS").children[cai_region_idx], "CAI_REGION").children[0].value)

    R = deep_copy(T)
    ch = R.children
    set_str(ch[0], spec.name)
    region_id = ids.obj()
    set_int(ch[4], region_id)

    # population / wealth
    pop = ch[1]
    rf = pop.children[0]
    rfp = spec.region_factors_pop or spec.population[:2]
    set_int(rf.children[2], rfp[0])
    set_int(rf.children[4], rfp[1])
    for i, v in zip((1, 2, 3), spec.population):
        set_int(pop.children[i], v)
    for i in (9, 12, 13, 14, 16, 17):
        set_int(ch[i], 0)
    if spec.wealth is not None:
        set_int(ch[10], spec.wealth[0])
        set_int(ch[11], spec.wealth[1])
    set_int(ch[15], spec.town_growth)
    if spec.wealth_distribution is not None:
        set_list(ch[31], list(spec.wealth_distribution))
    set_list(ch[32], [False] * len(ch[32].value))
    # religion: the template's mix (armenia = 90% orthodox) under an Islamic owner is a
    # turn-one revolt; URR gave its Arabia the owner's majority religion
    rb = find(rf, "RELIGION_BREAKDOWN")
    shares = dict(spec.religion)
    for item in rb.children:
        item[1].value, item[1].raw = float(shares.pop(item[0].value, 0.0)), b""
    assert not shares, f"religions not present in template breakdown: {list(shares)}"

    # slots: rebuild from template slots of the same kind
    rsm = ch[3]
    tpl_slots = {}

    def has_building(region_slot_item) -> bool:
        return any(isinstance(c, ESFNode) for c in region_slot_item[0].children[1].children)

    for it in T.children[3].children[0].children:
        s = it[0]
        kind = slot_kind(s.children[3].value)
        if kind == "port":
            continue
        # towns: prefer an undeveloped template (plain "town" DB type has no building yet)
        if kind == "town" and (kind not in tpl_slots or (has_building(tpl_slots[kind]) and not has_building(it))):
            tpl_slots[kind] = it
        else:
            tpl_slots.setdefault(kind, it)
    tpl_slots["settlement_road"] = T.children[3].children[1]
    tpl_slots["settlement_fortification"] = T.children[3].children[2]

    slot_ids: dict[str, int] = {}

    def prep_slot(region_slot: ESFNode, s: SlotSpec) -> int:
        sid = ids.obj()
        sgr = region_slot.children[0]
        set_int(sgr.children[1], sid)
        set_int(find(sgr, "GARRISON_RESIDENCE").children[0], owner_id)
        set_int(region_slot.children[2], sid)
        set_str(region_slot.children[3], s.key)
        set_int(region_slot.children[6], fixed(s.pos[0]))
        set_int(region_slot.children[7], fixed(s.pos[1]))
        slot_ids[s.key] = sid
        return sid

    new_items = []
    for s in spec.slots:
        if s.kind in ("settlement_road", "settlement_fortification"):
            continue
        it = deep_copy(tpl_slots[s.kind])
        prep_slot(it[0], s)
        new_items.append(it)
    rsm.children[0].children = new_items
    for s in spec.slots:
        if s.kind == "settlement_road":
            rsm.children[1] = deep_copy(tpl_slots["settlement_road"])
            prep_slot(rsm.children[1].children[1], s)
        if s.kind == "settlement_fortification":
            rsm.children[2] = deep_copy(tpl_slots["settlement_fortification"])
            prep_slot(rsm.children[2].children[1], s)

    # settlement
    st = ch[5]
    settlement_id = ids.obj()
    sgr = st.children[0]
    set_int(find(sgr, "GARRISON_RESIDENCE").children[0], owner_id)
    set_int(sgr.children[1], settlement_id)
    set_int(sgr.children[10], fixed(spec.capital[0]))
    set_int(sgr.children[11], fixed(spec.capital[1]))
    set_str(st.children[1].children[0], f"start_pos_settlements_onscreen_name_{spec.settlement_key}")
    if spec.settlement_tier is None:
        spec.settlement_tier = st.children[2].value
    set_int(st.children[2], spec.settlement_tier)
    set_str(st.children[3], spec.settlement_key)
    set_int(st.children[4], settlement_id)
    set_str(st.children[5], spec.settlement_key)

    set_int(ch[19], owner_id)
    los = ch[20]
    if los_bbox is not None and len(los.children) >= 3:
        set_v2(los.children[1], los_bbox[0])
        set_v2(los.children[2], los_bbox[1])
    else:
        # vanilla's minimal LINE_OF_SIGHT is just [False] (persia, tejas, labrador...);
        # a full one carries a visibility bitmap of the TEMPLATE's area, which is wrong here
        set_bool(los.children[0], False)
        los.children = los.children[:1]
    set_str(ch[22], spec.theatre_name)
    set_str(ch[23], spec.emergent_nation)
    set_str(ch[24], spec.rebels_name)
    set_str(ch[25], spec.culture)
    recruit_id = ids.obj()
    tpl_recruit_id = T.children[26].children[2].value
    set_int(ch[26].children[2], recruit_id)
    ch[35].children.clear()
    if spec.resources is not None:
        proto = ch[36].children[0]
        ch[36].children = [[ESFPrimitive(type_tag=proto[0].type_tag, value=r, raw=b"")] for r in spec.resources]
    set_str(ch[37], "")
    set_int(ch[38], spec.prestige)
    set_str(ch[40].children[0], f"regions_onscreen_{spec.name}")
    ra.children.append([R])
    print(f"startpos: REGION {spec.name} appended at {len(ra.children) - 1}, region_id {region_id:#x}, "
          f"owner {spec.owner_faction} ({owner_id})")

    # governorship post of the owner: same post the template uses
    tpl_region_id = T.children[4].value
    gov_post_id = T.children[21].value
    mirrored = 0
    for g in find_all(root, "GOVERNORSHIP"):
        if g.children[1].value == gov_post_id:
            lst = list(g.children[2].value)
            assert tpl_region_id in lst
            set_list(g.children[2], lst + [region_id])
            mirrored += 1
    assert mirrored == 1, f"expected one GOVERNORSHIP with id {gov_post_id}, found {mirrored}"

    # every other pure id-list that mentions the template's region or recruitment id
    def mirror(old, new, skip_tags=("GOVERNORSHIP",)):
        n = 0
        st_ = [(root, ())]
        while st_:
            x, p = st_.pop()
            if isinstance(x, ESFNode):
                for c in x.children:
                    if isinstance(c, ESFPrimitive):
                        v = c.value
                        if isinstance(v, (list, tuple)) and old in v and x.tag not in skip_tags and 0 not in v:
                            set_list(c, list(v) + [new])
                            n += 1
                    else:
                        st_.append((c, p + (x.tag,)))
            elif isinstance(x, list):
                for c in x:
                    if isinstance(c, ESFPrimitive):
                        v = c.value
                        if isinstance(v, (list, tuple)) and old in v and 0 not in v:
                            set_list(c, list(v) + [new])
                            n += 1
                    else:
                        st_.append((c, p))
        return n

    print(f"  mirrored region_id into {mirror(tpl_region_id, region_id)} lists, "
          f"recruitment id into {mirror(tpl_recruit_id, recruit_id)} lists")

    # ── CAI ────────────────────────────────────────────────────────────────
    cws = find(root, "CAI_WORLD_SETTLEMENTS")
    cbs = find(root, "CAI_WORLD_BUILDING_SLOTS")
    crs = find(root, "CAI_WORLD_REGION_SLOTS")
    tpl_settlement_id = T.children[5].children[4].value
    tpl_cws = next(it for it in cws.children if find(it, "CAI_SETTLEMENT").children[2].value == tpl_settlement_id)
    tpl_cai_settlement = tpl_cws[3].value

    def bslot_item(slot_id):
        return next(it for it in cbs.children if find(it, "CAI_BUILDING_SLOT").children[0].value == slot_id)

    def rslot_item(cai_id):
        return next(it for it in crs.children if it[3].value == cai_id)

    tpl_slot_by_kind = {}
    for it in T.children[3].children[0].children:
        kind = slot_kind(it[0].children[3].value)
        if kind == "port":
            continue
        # same choice of template slot as the REGION clone above (towns: undeveloped)
        if kind in tpl_slots and tpl_slots[kind] is not it:
            continue
        tpl_slot_by_kind.setdefault(kind, it[0].children[2].value)
    tpl_slot_by_kind["settlement_road"] = T.children[3].children[1].children[1].children[2].value
    tpl_slot_by_kind["settlement_fortification"] = T.children[3].children[2].children[1].children[2].value

    cai_settlement = ids.cai()
    settlement_bslots = []
    region_slot_ids = []
    for s in spec.slots:
        tpl_slot_id = tpl_slot_by_kind[s.kind]
        tb = bslot_item(tpl_slot_id)
        b = deep_copy(tb)
        bid = ids.cai()
        set_int(b[1], bid)
        clear_bdi(b)
        cbsr = find(b, "CAI_BUILDING_SLOT")
        set_int(cbsr.children[0], slot_ids[s.key])
        if s.kind.startswith("settlement_"):
            set_int(cbsr.children[2], cai_settlement)
            settlement_bslots.append(bid)
        else:
            tpl_rslot = rslot_item(find(tb, "CAI_BUILDING_SLOT").children[2].value)
            r = deep_copy(tpl_rslot)
            rid = ids.cai()
            set_int(r[0].children[0], cai_region_ai_id)
            sit = r[1]
            set_int(sit.children[0], fixed(s.pos[0]))
            set_int(sit.children[1], fixed(s.pos[1]))
            set_int(sit.children[2], cai_region_ai_id)
            set_list(sit.children[3], theatre_id)
            set_int(r[3], rid)
            clear_bdi(r)
            crsr = find(r, "CAI_REGION_SLOT")
            set_int(crsr.children[1], bid)
            for extra in crsr.children:
                if isinstance(extra, ESFNode) and extra.tag == "CAI_SITUATED":
                    set_int(extra.children[0], fixed(s.pos[0]))
                    set_int(extra.children[1], fixed(s.pos[1]))
                    set_int(extra.children[2], cai_region_ai_id)
                    set_list(extra.children[3], theatre_id)
            set_int(cbsr.children[2], rid)
            crs.children.append(r)
            region_slot_ids.append(rid)
        cbs.children.append(b)

    c = deep_copy(tpl_cws)
    sit = c[1]
    set_int(sit.children[0], fixed(spec.capital[0]))
    set_int(sit.children[1], fixed(spec.capital[1]))
    set_int(sit.children[2], cai_region_ai_id)
    set_list(sit.children[3], theatre_id)
    set_int(c[3], cai_settlement)
    clear_bdi(c)
    cs = find(c, "CAI_SETTLEMENT")
    set_list(cs.children[0], settlement_bslots)
    set_int(cs.children[2], settlement_id)
    cws.children.append(c)

    cwr_item = find(root, "CAI_WORLD_REGIONS").children[cai_region_idx]
    cr = find(cwr_item, "CAI_REGION")
    assert cr.children[10].value == spec.name and cr.children[2].value == 0
    # wrapper OWNED_INDIRECT == settlement ai id for all 205 vanilla regions (0 when unsettled);
    # leaving it 0 crashed the end turn after the region changed hands (Empire.exe+0xbfdd0)
    set_int(cwr_item[0].children[0], cai_settlement)
    set_int(cr.children[2], cai_settlement)
    set_list(cr.children[3], region_slot_ids)
    set_int(cr.children[11], region_id)
    tpl_cr = next(find(it, "CAI_REGION") for it in find(root, "CAI_WORLD_REGIONS").children
                  if find(it, "CAI_REGION").children[10].value == spec.template)
    set_int(cr.children[12], tpl_cr.children[12].value)
    # vanilla invariant: every region's AI id is in its owner's CAI_FACTION[0] and in its
    # CAI_GOVERNORSHIP region list (France: 8 regions, 8 ids); new regions were missing
    cai_own_region(root, owner_id, cwr_item[2].value, T.children[21].value)
    print(f"  CAI: settlement {cai_settlement} (template {tpl_cai_settlement}), building slots {settlement_bslots}, "
          f"region slots {region_slot_ids}, governor ai {tpl_cr.children[12].value}")

    # CAMPAIGN_TRADE_MANAGER settlement node + land links: patch_trade_network()
    return owner_id, R


def parent_region(root, name):
    ra = find(root, "REGIONS_ARRAY")
    return next(find(it, "REGION") for it in ra.children if find(it, "REGION").children[0].value == name)


def take_parent_share(root, spec: RegionSpec) -> None:
    """A split region's population and wealth: its share of the parent's (set on the spec
    before patch_startpos); the parent keeps the rest (after)."""
    P = parent_region(root, spec.parent)
    pop, rf = P.children[1], P.children[1].children[0]
    ps, ws_ = spec.parent_share
    spec.population = tuple(int(pop.children[i].value * ps) for i in (1, 2, 3))
    spec.region_factors_pop = (int(rf.children[2].value * ps), int(rf.children[4].value * ps))
    spec.wealth = (int(P.children[10].value * ws_), int(P.children[11].value * ws_))


def transfer_startpos(root, spec: RegionSpec, cai_region_ai_id: int, R) -> None:
    """Move the parent's REGION_SLOT records to the split region (keys and positions
    updated, ids / buildings / AI history kept), re-point their CAI region slots, and
    reduce the parent's population and wealth by the child's share."""
    P = parent_region(root, spec.parent)
    moved = {sl.src: sl for sl in spec.moved}
    items = P.children[3].children[0].children
    take = [it for it in items if it[0].children[3].value in moved]
    assert len(take) == len(moved), f"{spec.parent} lacks some of {sorted(moved)}"
    staying = [it for it in take if moved[it[0].children[3].value].stay]
    p_ai = next(it[2].value for it in find(root, "CAI_WORLD_REGIONS").children
                if find(it, "CAI_REGION").children[10].value == spec.parent)
    P.children[3].children[0].children = [it for it in items if it not in take or it in staying]
    cbs = find(root, "CAI_WORLD_BUILDING_SLOTS")
    crs = find(root, "CAI_WORLD_REGION_SLOTS")
    cwr = find(root, "CAI_WORLD_REGIONS")
    crs_by_names = {find(it, "CAI_REGION").children[10].value: find(it, "CAI_REGION") for it in cwr.children}
    p_cr, c_cr = crs_by_names[spec.parent], crs_by_names[spec.name]
    theatre_id = list(c_cr.children[0].value)
    rids = []
    for it in take:
        rs = it[0]
        m = moved[rs.children[3].value]
        set_str(rs.children[3], m.key)
        set_int(rs.children[6], fixed(m.pos[0]))
        set_int(rs.children[7], fixed(m.pos[1]))
        b = next(x for x in cbs.children if find(x, "CAI_BUILDING_SLOT").children[0].value == rs.children[2].value)
        rid = find(b, "CAI_BUILDING_SLOT").children[2].value
        r = next(x for x in crs.children if x[3].value == rid)
        for sit in [r[1]] + [c for c in find(r, "CAI_REGION_SLOT").children if isinstance(c, ESFNode) and c.tag == "CAI_SITUATED"]:
            set_int(sit.children[0], fixed(m.pos[0]))
            set_int(sit.children[1], fixed(m.pos[1]))
            set_int(sit.children[2], cai_region_ai_id)
            set_list(sit.children[3], theatre_id)
        if m.stay:
            for sit in [r[1]] + [c for c in find(r, "CAI_REGION_SLOT").children if isinstance(c, ESFNode) and c.tag == "CAI_SITUATED"]:
                set_int(sit.children[2], p_ai)
                set_list(sit.children[3], list(p_cr.children[0].value))
            continue
        rids.append(rid)
    set_list(p_cr.children[3], [x for x in p_cr.children[3].value if x not in rids])
    set_list(c_cr.children[3], list(c_cr.children[3].value) + rids)
    R.children[3].children[0].children += [it for it in take if it not in staying]
    pop, rf = P.children[1], P.children[1].children[0]
    ps, ws_ = spec.parent_share
    for i in (1, 2, 3):
        set_int(pop.children[i], int(pop.children[i].value * (1 - ps)))
    for i in (2, 4):
        set_int(rf.children[i], int(rf.children[i].value * (1 - ps)))
    for i in (10, 11):
        set_int(P.children[i], int(P.children[i].value * (1 - ws_)))
    for key, src in (spec.slot_buildings or {}).items():
        ra = find(root, "REGIONS_ARRAY")
        rs_of = {it[0].children[3].value: it[0] for reg in ra.children
                 for it in find(reg, "REGION").children[3].children[0].children}
        moved_keys = {m.src: m.key for sp_ in SPECS.values() for m in sp_.moved}
        src_rs = rs_of.get(src) or rs_of[moved_keys[src]]
        dst = rs_of[key]
        dst.children[1] = deep_copy(src_rs.children[1])
        b = find(dst.children[1], "BUILDING")
        if b is not None:
            set_str(b.children[2], spec.owner_faction)
        print(f"  {key} starts with {src}'s building ({b.children[1].value if b is not None else 'none'})")
    print(f"  moved {len(take) - len(staying)} slot records from {spec.parent} (and {len(staying)} within it) ({[moved[k].key for k in moved]}), CAI region "
          f"slots {rids}; {spec.parent} keeps {1 - ps:.0%} of its population, {1 - ws_:.0%} of its wealth")


# ─── fort obstacles ───────────────────────────────────────────────

def region_ring_inside(regions_root, name):
    """Point-in-polygon test over the region's closed outlines."""
    rd = regions_root.children[3]
    vraw = rd.children[0].children[0].raw
    xy = struct.unpack(f"<{len(vraw) // 4}f", vraw)
    regs = rd.children[3]
    it = regs.children[[region_name(r) for r in regs.children].index(name)]
    areas = next(c for c in it if isinstance(c, ESFNode) and c.tag == "areas")
    rings = [[(xy[2 * v], xy[2 * v + 1]) for v in ol[3].value]
             for a in areas.children for ol in a[7].children if ol[0].value]
    pts = [p for ring in rings for p in ring]
    bx0, by0 = min(p[0] for p in pts), min(p[1] for p in pts)
    bx1, by1 = max(p[0] for p in pts), max(p[1] for p in pts)

    def inside(px, py):
        if not (bx0 <= px <= bx1 and by0 <= py <= by1):
            return False
        for ring in rings:
            c = False
            n = len(ring)
            for i in range(n):
                x1, y1 = ring[i]
                x2, y2 = ring[(i + 1) % n]
                if (y1 > py) != (y2 > py) and px < x1 + (py - y1) * (x2 - x1) / (y2 - y1):
                    c = not c
            if c:
                return True
        return False
    return inside


def find_fort_object(root, oid):
    """The FORT_ARRAY item whose fort object id is oid, and its owning region."""
    for it in find(root, "REGIONS_ARRAY").children:
        R = find(it, "REGION")
        for f in R.children[35].children:
            if f[1].children[1].value == oid:
                return R, f
    return None, None


def cai_faction_item(root, fid: int):
    """CAI_WORLD_FACTIONS item whose CAI_FACTION[6] is the faction id."""
    return next(it for it in find(root, "CAI_WORLD_FACTIONS").children if find(it, "CAI_FACTION").children[6].value == fid)


def cai_governorship(root, gov_id: int):
    return next(find(it, "CAI_GOVERNORSHIP") for it in find(root, "CAI_WORLD_GOVERNORSHIPS").children
                if find(it, "CAI_GOVERNORSHIP").children[0].value == gov_id)


def _list_add(p, x):
    v = list(p.value)
    if x not in v:
        set_list(p, v + [x])


def _list_remove(p, x):
    set_list(p, [y for y in p.value if y != x])


def cai_own_region(root, fid: int, cai_region: int, gov_id: int) -> None:
    _list_add(find(cai_faction_item(root, fid), "CAI_FACTION").children[0], cai_region)
    _list_add(cai_governorship(root, gov_id).children[3], cai_region)


# Ownership transfers of VANILLA regions (user decision 2026-10-03, "option 1", URR's
# choice). Labrador, great_plains and new_mexico reach the rest of the map only through
# void regions, so they have no land edge in the engine's transport graph; when one
# faction owns both such a region and the adjacent new region, the AI asks for a land
# leg that cannot exist and campaign load crashes (Empire.exe+0x596e02, deep_dive 8.18).
# (region, new owner, a region of the new owner whose governorship it joins,
#  new region that becomes the old owner's capital if the region was its capital,
#  new region whose presence triggers the transfer)
TRANSFERS = [
    ("labrador", "france", "new_france", "wilderness_canada", "wilderness_canada"),
    ("new_mexico", "spain", "florida", None, "wilderness_mexico"),
]


def transfer_region(root, name: str, new_owner: str, gov_like: str, capital_to: str | None) -> None:
    ra = find(root, "REGIONS_ARRAY")
    regs = {sp_region_name(it): find(it, "REGION") for it in ra.children}
    R, G = regs[name], regs[gov_like]
    old_fid, new_fid = R.children[19].value, faction_id(root, new_owner)
    rid, old_gov, new_gov = R.children[4].value, R.children[21].value, G.children[21].value
    # REGION: owner, governorship, every garrison residence / fort owner inside it
    n_own = 0
    for gr in find_all(R, "GARRISON_RESIDENCE"):
        if gr.children[0].value == old_fid:
            set_int(gr.children[0], new_fid)
            n_own += 1
    for f in R.children[35].children:
        if f[0].value == old_fid:
            set_int(f[0], new_fid)
    set_int(R.children[19], new_fid)
    set_int(R.children[21], new_gov)
    # governorship posts
    for g in find_all(root, "GOVERNORSHIP"):
        if g.children[1].value == old_gov:
            _list_remove(g.children[2], rid)
        elif g.children[1].value == new_gov:
            _list_add(g.children[2], rid)
    # the old owner's FACTION capital fields
    new_cap_rid = regs[capital_to].children[4].value if capital_to else None
    for it in find(root, "FACTION_ARRAY").children:
        f = it[0]
        if not any(isinstance(c, ESFPrimitive) and c.value == old_fid for c in f.children[:14]):
            continue
        for c in f.children:
            if isinstance(c, ESFPrimitive) and isinstance(c.value, int) and c.value == rid:
                assert new_cap_rid, f"{name} is {old_fid}'s capital; give capital_to"
                set_int(c, new_cap_rid)
    # AI: region, governorship lists, faction lists and capital, settlement owner
    cwr = {find(it, "CAI_REGION").children[10].value: it for it in find(root, "CAI_WORLD_REGIONS").children}
    cai_rid = cwr[name][2].value
    cr = find(cwr[name], "CAI_REGION")
    set_int(cr.children[12], find(cwr[gov_like], "CAI_REGION").children[12].value)
    _list_remove(cai_governorship(root, old_gov).children[3], cai_rid)
    _list_add(cai_governorship(root, new_gov).children[3], cai_rid)
    old_cf, new_cf = cai_faction_item(root, old_fid), cai_faction_item(root, new_fid)
    old_cai_fac, new_cai_fac = old_cf[2].value, new_cf[2].value
    _list_remove(find(old_cf, "CAI_FACTION").children[0], cai_rid)
    _list_add(find(new_cf, "CAI_FACTION").children[0], cai_rid)
    cap_cai = cwr[capital_to][2].value if capital_to else None
    for c in find(old_cf, "CAI_FACTION").children:
        if isinstance(c, ESFPrimitive) and isinstance(c.value, int) and c.value == cai_rid:
            assert cap_cai, f"{name} is the old owner's AI capital; give capital_to"
            set_int(c, cap_cai)
    sett_id = R.children[5].children[4].value
    for it in find(root, "CAI_WORLD_SETTLEMENTS").children:
        cs = find(it, "CAI_SETTLEMENT")
        if cs.children[2].value == sett_id:
            set_int(it[0].children[0], new_cai_fac)          # OWNED_DIRECT
            if cs.children[1].value == old_cai_fac:          # faction-capital marker
                set_int(cs.children[1], 0)
    print(f"transfer: {name} -> {new_owner} (governorship {old_gov} -> {new_gov}, {n_own} garrison residences"
          f"{', old owner capital -> ' + capital_to if capital_to else ''})")


def patch_trade_network(root, new_names: list[str]) -> None:
    """Give new regions (and the vanilla regions only they connect) land links in the
    engine's transport graph, CAMPAIGN_TRADE_MANAGER (deep_dive 8.20).

    Node ids: ports 0..131 (PORT_INDICES), settlements 132..266 (SETTLEMENT_INDICES),
    then the 20 TRADE_NODES (sea waypoints, 267..286). New settlements used to take
    max+1 = 267.. and so aliased sea waypoints. TRADE_ROUTES [a<b, [segments], length]
    are the graph's links; vanilla has none for great_plains/new_mexico (not even
    settlement nodes), so an AI route touching them crashed (Empire.exe+0x64f66f) and a
    same-owner leg into Labrador found no link (+0x596e02). URR's fix, copied here: move
    the trade nodes up (we instead number new nodes above every id), add settlement nodes, and add a segment-less link to every land
    neighbour (60 of vanilla's 228 land links have no segments either). Length = the
    straight distance between the two settlements (URR uses a flat 227.27)."""
    tm = find(root, "CAMPAIGN_TRADE_MANAGER")
    si, tn, tr = find(tm, "SETTLEMENT_INDICES"), find(tm, "TRADE_NODES"), find(tm, "TRADE_ROUTES")
    node = {it[0].value: it[1].value for it in si.children}
    regs = {sp_region_name(it): find(it, "REGION") for it in find(root, "REGIONS_ARRAY").children}
    adj: dict[str, set] = {}
    cid = {}
    for it in find(root, "CAI_WORLD_REGIONS").children:
        cid[it[2].value] = find(it, "CAI_REGION").children[10].value
    for b in find_all(root, "CAI_REGION_BOUNDARY"):
        x, y = cid.get(b.children[0].value), cid.get(b.children[1].value)
        if x and y:
            adj.setdefault(x, set()).add(y)
            adj.setdefault(y, set()).add(x)
    # settled regions that need a node: the new ones, and vanilla settled regions the
    # vanilla graph left out because every land neighbour was void
    want = [n for n in new_names if n not in node]
    linked = set(node) | set(want)
    want += sorted(n for n in regs if n not in node and n not in want and adj.get(n, set()) & linked)
    # New settlement nodes go above EVERY existing node id. Node ids are only map keys
    # (URR runs settlements, ports and waypoints out of order), and moving existing ids
    # is unsafe: stored DOMESTIC_TRADE_ROUTE [6]/[7] and INTERNATIONAL_TRADE_ROUTE [3]/[4]
    # name waypoints and settlements by id (shifting the waypoints left Portugal's route
    # to waypoint 273 pointing at a new settlement: crash Empire.exe+0x66b08b).
    all_ids = (set(node.values()) | {it[1].value for it in tn.children}
               | {it[1].value for it in find(tm, "PORT_INDICES").children})
    first = max(all_ids) + 1
    for k, name in enumerate(want):
        e = deep_copy(si.children[0])
        set_str(e[0], name)
        set_int(e[1], first + k)
        si.children.append(e)
        node[name] = first + k

    def pos(name):
        sgr = find(regs[name].children[5], "SIEGEABLE_GARRISON_RESIDENCE")
        return sgr.children[10].value / FIXED, sgr.children[11].value / FIXED

    have = {(r[0].value, r[1].value) for r in tr.children}
    proto = next(r for r in tr.children if not list(r[2].value))
    added = []
    for name in want:
        for other in sorted(adj.get(name, ())):
            if other not in node or other == name:
                continue
            a, b = sorted((node[name], node[other]))
            if (a, b) in have:
                continue
            (x1, y1), (x2, y2) = pos(name), pos(other)
            r = deep_copy(proto)
            set_int(r[0], a)
            set_int(r[1], b)
            set_list(r[2], [])
            r[3].value, r[3].raw = math.hypot(x2 - x1, y2 - y1), b""
            tr.children.append(r)
            have.add((a, b))
            added.append(f"{name}-{other}")
    # a settlement with no settled land neighbour (Borneo) would be an isolated node, and
    # any AI route to it fails like +0x596e02; link it to the nearest sea waypoint (the
    # Dutch domestic route already ends at Borneo's, 876.9,-70.1). URR gives Borneo a port.
    linked_now = {n for n in want if any(x.startswith(n + "-") or x.endswith("-" + n) for x in added)}
    for name in want:
        if name in linked_now:
            continue
        x1, y1 = pos(name)
        wp, (wx, wy) = min(((it[1].value, tuple(it[0].value)) for it in tn.children),
                           key=lambda t: math.hypot(t[1][0] - x1, t[1][1] - y1))
        a_, b_ = sorted((node[name], wp))
        if (a_, b_) not in have:
            r = deep_copy(proto)
            set_int(r[0], a_)
            set_int(r[1], b_)
            set_list(r[2], [])
            r[3].value, r[3].raw = math.hypot(wx - x1, wy - y1), b""
            tr.children.append(r)
            have.add((a_, b_))
            added.append(f"{name}-waypoint{wp}")
    print(f"trade network: settlement nodes {', '.join(f'{n}={node[n]}' for n in want)}; "
          f"{len(added)} land links: {', '.join(added)}")


def resituate(root, regions_root, specs) -> None:
    """Every CAI_SITUATED (the AI's position record for armies, agents, slots: x, y, region
    AI id, theatre ids) standing in a split-off region but still naming its parent is moved
    to the child. transfer_startpos does this for moved slots only; the two French armies
    near Lyon stayed 'in France' inside the Lyonnais / Burgundy, and selecting one hung the
    game (deep_dive 10.10)."""
    cwr = {find(it, "CAI_REGION").children[10].value: it for it in find(root, "CAI_WORLD_REGIONS").children}
    moved = []
    for spec in specs:
        if spec.parent is None:
            continue
        inside = region_ring_inside(regions_root, spec.name)
        p_ai, c_ai = cwr[spec.parent][2].value, cwr[spec.name][2].value
        theatre = list(find(cwr[spec.name], "CAI_REGION").children[0].value)
        for sit in find_all(root, "CAI_SITUATED"):
            if sit.children[2].value == p_ai and inside(sit.children[0].value / FIXED, sit.children[1].value / FIXED):
                set_int(sit.children[2], c_ai)
                set_list(sit.children[3], theatre)
                moved.append(f"{spec.name}@({sit.children[0].value / FIXED:.2f},{sit.children[1].value / FIXED:.2f})")
    print(f"CAI_SITUATED re-pointed to the split region they stand in: {len(moved)} {moved}")


def trade_endpoint_regions(root) -> dict[int, int]:
    """Transport-graph node id -> REGION id of the region that holds it now: a port node by
    the region whose REGION_SLOT list carries its key, a settlement node by its region.
    Sea waypoints (TRADE_NODES) belong to no region."""
    tm = find(root, "CAMPAIGN_TRADE_MANAGER")
    rid_by_name, rid_by_slot = {}, {}
    for it in find(root, "REGIONS_ARRAY").children:
        R = find(it, "REGION")
        rid_by_name[R.children[0].value] = R.children[4].value
        for s in R.children[3].children[0].children:
            rid_by_slot[s[0].children[3].value] = R.children[4].value
    out = {it[1].value: rid_by_slot[it[0].value] for it in find(tm, "PORT_INDICES").children}
    out.update({it[1].value: rid_by_name[it[0].value] for it in find(tm, "SETTLEMENT_INDICES").children})
    return out


def stale_trade_routes(root) -> list[tuple]:
    """Stored INTERNATIONAL_TRADE_ROUTEs whose region id is not the region holding the
    endpoint it is paired with: [1] goes with start node [3], [6] with end node [4]
    (vanilla: 74 of 74 match, including Mughal's route from Mesopotamia). On load the
    engine looks the node up among that region's ports and settlements and hashes the
    result, so a stale one is a null-key crash (Empire.exe+0x51470 via +0x5e0bf6)."""
    where = trade_endpoint_regions(root)
    bad = []
    for r in find_all(root, "INTERNATIONAL_TRADE_ROUTE"):
        for ri, ni in ((1, 3), (6, 4)):
            want = where.get(r.children[ni].value)
            if want is not None and r.children[ri].value != want:
                bad.append((r, ri, ni, want))
    return bad


def patch_trade_routes(root) -> None:
    """Re-point stored trade routes at the region now holding their endpoint. A split
    moves the parent's port slots into the child under their old keys (port:france:
    le_havre is Normandy's), and France's, Louisiana's and Sweden's routes through Le Havre
    still named France: batch1_s3_unlocked crashed once the loading bar finished."""
    bad = stale_trade_routes(root)
    for r, ri, ni, want in bad:
        set_int(r.children[ri], want)
    print(f"trade routes: {len(bad)} region ids re-pointed to the endpoint's region "
          f"(nodes {sorted({r.children[ni].value for r, _, ni, _ in bad})})")
    assert not stale_trade_routes(root)


# Settlement / slot footprints in pathfinding.esf (deep_dive 7.8, 7.9, 8.16).
# Every vanilla settlement and slot carries one; void regions carry none, which is
# why armies can enter a new settlement but never leave it, and why the engine
# finds no route out of a new region (crash Empire.exe+0x596e02, labrador ->
# wilderness_canada). The template is Pensa's wheat-slot octagon: 2 cells, 4
# records, no shared vertices with anything else (settlement footprints are welded
# to the road network and cannot be lifted, 7.9). It is used for the capital too.
FOOTPRINT_TEMPLATE = (2, (330.9932861328125, 374.089111328125))   # grid, wheat:tatariya:pensa
VANILLA_TOWNS_DB = Path("data/lord_mod/vanilla_db/campaign_map_towns_and_ports_tables/campaign_map_towns_and_ports")
FOOTPRINT_RADIUS = 1


def place_footprints(specs, pf_root, sp_root, regions_root) -> dict[int, AreaGrid]:
    """Snap each new capital / town / resource slot to the nearest clean spot that is
    the template plus whole cells, and transplant the template footprint there.
    Moves spec positions (the settlement_* building slots follow the capital), so it
    must run before regions.esf and startpos are patched. Cells that carry a startpos
    obstacle node are avoided, so no obstacle entry ever describes an edited cell.
    Returns the edited grids."""
    grids: dict[int, AreaGrid] = {}

    def grid(gi):
        if gi not in grids:
            grids[gi] = AreaGrid(pf_root.children[0].children[gi])
        return grids[gi]

    src_gi, tpl = FOOTPRINT_TEMPLATE
    src = grid(src_gi)
    reserved: dict[int, set] = {}
    # split regions may cut obstacle cells whose startpos entries are plain copies (they are
    # re-copied at the end); snapshot which those are before any cell changes
    from etwpc.compiler.obstacles import obstacle_copies, refresh_obstacle_copies
    obs: dict[int, tuple] = {}
    for spec in specs:
        if spec.footprints == "carve" and spec.pf_grid not in obs:
            o = ObstacleSystem(find(sp_root, "CAMPAIGN_PATHFINDER").children[0].children[spec.pf_grid])
            obs[spec.pf_grid] = (o,) + obstacle_copies(o, grid(spec.pf_grid))

    def geometry(g_, rc):        # a cell's records without pass / unknown2
        from etwpc.io.esf_types import BoundaryEntry
        k, j = g_.cell_item[rc[0] * g_.cols + rc[1]]
        it = g_.items[k]
        if j or not it.bounds:
            return ("run", it.pid)
        return tuple((b.path_type, b.path_id, b.vertex_index)
                     for b in (BoundaryEntry.from_packed(a, c) for a, c in it.bounds))
    before = {gi: {rc: geometry(grid(gi), rc) for rc in v[2]} for gi, v in obs.items()}
    blocks: dict[int, list] = {}          # grid -> transplanted footprint centres
    for spec in specs:
        if not spec.footprints:
            print(f"footprint {spec.name}: none (spec.footprints = False)")
            continue
        g = grid(spec.pf_grid)
        if spec.footprints == "coastal":
            place_coastal_footprints(spec, g, sp_root, regions_root)
            continue
        if spec.footprints == "carve":
            place_split_footprints(spec, g, sp_root, regions_root, avoid=obs[spec.pf_grid][2])
            continue
        if spec.pf_grid not in reserved:
            osys = ObstacleSystem(find(sp_root, "CAMPAIGN_PATHFINDER").children[0].children[spec.pf_grid])
            reserved[spec.pf_grid] = {(p >> 16, p & 0xFFFF) for p, _ in osys.pairs}
        res = reserved[spec.pf_grid]
        inside = region_ring_inside(regions_root, spec.name)

        def label(xy):
            # the label the cells around xy actually carry (a slot near a border may sit on
            # a neighbour's cells)
            r, c = g.cell_of(*xy)
            cnt = Counter(g.kind(rr, cc)[1] for rr in range(r - 3, r + 4) for cc in range(c - 3, c + 4)
                          if g.kind(rr, cc)[0] != "hdr")
            return cnt.most_common(1)[0][0]

        def place(xy, prefer=None):
            # try the capital's land label first: a coastal slot's neighbourhood is
            # mostly sea-labelled (Borneo's Tabanio, 2026-10-03)
            for pid in dict.fromkeys(x for x in (prefer, label(xy)) if x is not None):
                for ring in (6, 12):
                    try:
                        new_xy = find_clean_target(g, tpl, xy, FOOTPRINT_RADIUS, pid, res, inside, max_ring=ring)
                    except RuntimeError:
                        continue
                    g.transplant(tpl, new_xy, FOOTPRINT_RADIUS, pid, src=src)
                    blocks.setdefault(spec.pf_grid, []).append(new_xy)
                    return new_xy, pid
            return None, label(xy)

        old_cap = spec.capital
        new_cap, pid = place(old_cap)
        cap_pid = pid
        if new_cap is None:
            raise RuntimeError(f"{spec.name}: no clean footprint spot near the capital {old_cap}")
        spec.capital = new_cap
        ddx, ddy = new_cap[0] - old_cap[0], new_cap[1] - old_cap[1]
        print(f"footprint {spec.name}: capital {old_cap} -> {new_cap} (label {pid})")
        for sl in spec.slots:
            if sl.kind.startswith("settlement_"):
                sl.pos = (sl.pos[0] + ddx, sl.pos[1] + ddy)
                continue
            if sl.kind not in ("town", "resource"):
                continue
            new_xy, pid = place(sl.pos, prefer=cap_pid)
            if new_xy is None:
                print(f"   WARNING {sl.key}: no clean spot near {sl.pos} (label {pid}); slot left without footprint")
                continue
            print(f"   {sl.key}: {sl.pos} -> {new_xy} (label {pid})")
            sl.pos = new_xy
    # a block keeps its template's links to neighbouring cells; where a neighbour here is
    # a header cell instead of interior, recompute them (Khiva's Astrabad, deep_dive 9.11).
    # Only the block's own records: the rules miss a few vanilla ones in other cells.
    from etwpc.compiler.coastal import CellView, refresh_fields
    for gi, centres in sorted(blocks.items()):
        cells = set()
        for xy in centres:
            r0, c0 = grids[gi].cell_of(*xy)
            cells |= {(r, c) for r in range(r0 - FOOTPRINT_RADIUS, r0 + FOOTPRINT_RADIUS + 1)
                      for c in range(c0 - FOOTPRINT_RADIUS, c0 + FOOTPRINT_RADIUS + 1)}
        n = refresh_fields(CellView(grids[gi]), sorted(cells))
        print(f"footprints grid {gi}: {n} transplanted record field(s) recomputed")
    for gi, (o, copies, reshaped) in obs.items():
        moved = sorted(rc for rc in reshaped if geometry(grids[gi], rc) != before[gi][rc])
        if moved:
            print(f"WARNING footprints grid {gi}: {len(moved)} cells with reshaped obstacle entries changed "
                  f"geometry: {moved[:10]}")
        n = refresh_obstacle_copies(o, grids[gi], copies, reshaped)
        o.flush()
        print(f"footprints grid {gi}: {n} obstacle copies re-copied from their edited cells")
    edited = {s.pf_grid for s in specs}
    return {gi: g for gi, g in grids.items() if gi in edited}


def template_outline(regions_root, spec: RegionSpec, kind: str):
    """(float32 polygon bytes, anchor) that patch_regions_esf translates for a footprint:
    the template's settlement polygon for kind 'capital', else its first slot of `kind`."""
    regs = regions_root.children[3].children[3]
    tsas = sas_of(next(it for it in regs.children if region_name(it) == spec.template))
    if kind == "capital":
        return tsas.children[1].raw, tsas.children[0].value
    t = next(sl for sl in tsas.children[2].children if slot_kind(sl[0].value) == kind)
    return t[6].raw, t[2].value


def place_coastal_footprints(spec, g: AreaGrid, sp_root, regions_root) -> None:
    """Islands with no interior cells (deep_dive 9.8-9.11). In vanilla a slot outline IS
    the regions.esf footprint polygon (settlement_and_slots[1] for the settlement, slot[6]
    for a town / resource slot), carved out of the land and impassable records under it
    and never touching a sea record. So each outline here is the template polygon that
    patch_regions_esf will write, placed at the nearest site to the desired point where it
    fits that rule (coastal.find_outline_site), and carved in place. The settlement_*
    building slots follow the capital. Sites avoid cells that carry a startpos obstacle
    node, and so do their 4-neighbours (their fields are recomputed). Refuses to edit if
    the encoding rules do not reproduce the stored fields around a site, or if the edited
    cells break a vanilla invariant afterwards."""
    from shapely.geometry import Polygon
    from etwpc.compiler.coastal import (CellView, carve_footprint, check_cells, check_nodes,
                                        find_outline_site, ring_of, self_test)
    osys = ObstacleSystem(find(sp_root, "CAMPAIGN_PATHFINDER").children[0].children[spec.pf_grid])
    avoid = frozenset((p >> 16, p & 0xFFFF) for p, _ in osys.pairs)
    rd = regions_root.children[3].children[3]
    pid = g.pid_of([region_name(r) for r in rd.children].index(spec.name))
    inside = region_ring_inside(regions_root, spec.name)
    taken = []

    def place(xy, label, kind, min_land):
        raw, anchor = template_outline(regions_root, spec, kind)

        def make(x, y):
            f = struct.unpack(f"<{len(raw) // 4}f", translate_footprint(raw, anchor, (x, y)))
            return Polygon(list(zip(f[0::2], f[1::2])))

        view = CellView(g)
        x, y, outline = find_outline_site(view, make, xy, pid, inside, min_land=min_land, taken=taken, avoid=avoid)
        r0, c0 = g.cell_of(x, y)
        around = [(r, c) for r in range(r0 - 4, r0 + 5) for c in range(c0 - 4, c0 + 5)]
        bad = self_test(view, around)
        assert not bad, f"{spec.name} {label}: encoding rules do not reproduce cells near {(x, y)}: {bad[:3]}"
        res = carve_footprint(view, outline, pid)
        bad = check_cells(view, res["cells"] + sorted(ring_of(res["cells"]))) + check_nodes(view, res["cells"])
        bad += [f"cell {rc}: land record {i} reaches nothing" for rc in res["cells"]
                for i, q in enumerate(view.get(*rc)) if q.t == 0 and not q.be.passable_part and not q.be.unknown2]
        assert not bad, f"{spec.name} {label}: carved cells break a vanilla invariant: {bad[:3]}"
        taken.append(outline)
        print(f"   coastal footprint {label}: {xy} -> ({x:.3f}, {y:.3f}), outline area {res['area']:.3f} over "
              f"cells {res['cells']}; {res['records_changed']} records rewritten in {res['rewritten_cells']} cells, "
              f"{res['vertices_added']} vertices added")
        return (x, y)

    old_cap = spec.capital
    spec.capital = place(old_cap, "capital", "capital", min_land=0.7)
    ddx, ddy = spec.capital[0] - old_cap[0], spec.capital[1] - old_cap[1]
    print(f"footprint {spec.name}: capital {old_cap} -> {spec.capital} (coastal, path id {pid})")
    for sl in spec.slots:
        if sl.kind.startswith("settlement_"):
            sl.pos = (sl.pos[0] + ddx, sl.pos[1] + ddy)
        elif sl.kind in ("town", "resource"):
            try:
                sl.pos = place(sl.pos, sl.key, sl.kind, min_land=0.5)
            except RuntimeError as e:
                print(f"   WARNING {sl.key}: {e}; slot left without footprint")


def place_split_footprints(spec, g: AreaGrid, sp_root, regions_root, avoid=frozenset()) -> None:
    """Footprints of a region split off a live one (deep_dive 10.5), by the vanilla rule
    (a slot outline IS its regions.esf polygon, 9.11), on land or coast: a moved slot's
    old outline is merged back into land, then the capital's settlement polygon (the
    template's, as patch_regions_esf writes it) and each relocated slot's own polygon are
    carved at the nearest valid site. Slots that stay where they are keep their outlines
    (split_region.py relabelled them). Interior run cells may be cut, and so may obstacle
    cells whose entries are plain copies; `avoid` = cells holding reshaped entries. Their
    4-neighbours may only have pass / unknown2 recomputed, which leaves a reshaped entry's
    geometry valid, so they are allowed."""
    from shapely.geometry import Polygon
    from etwpc.compiler.coastal import (CellView, carve_footprint, check_cells, check_nodes,
                                        find_outline_site, outline_cells, ring_of, self_test,
                                        uncarve_footprint)
    rd = regions_root.children[3].children[3]
    names = [region_name(r) for r in rd.children]
    pid = g.pid_of(names.index(spec.name))
    ppid = g.pid_of(names.index(spec.parent))
    pslots = {sl[0].value: sl for sl in sas_of(rd.children[names.index(spec.parent)]).children[2].children}
    inside = region_ring_inside(regions_root, spec.name)
    p_inside = region_ring_inside(regions_root, spec.parent)

    def poly_at(raw, anchor, xy):
        f = struct.unpack(f"<{len(raw) // 4}f", translate_footprint(raw, anchor, xy))
        return Polygon(list(zip(f[0::2], f[1::2])))

    taken = []
    if spec.capital_slot:
        # a capital_slot outline in cells the startpos obstacles reshape stays where it is
        # and becomes the settlement's outline (no cell there may change)
        src = pslots[spec.capital_slot]
        here = tuple(src[2].value)
        if any(rc in avoid for rc in outline_cells(g, poly_at(src[6].raw, here, here))):
            spec.capital_raw = bytes(src[6].raw)
            taken.append(poly_at(src[6].raw, here, here))
            print(f"   footprint {spec.capital_slot}: outline kept as the capital's (obstacle-reshaped cells)")
    for sl in spec.moved:
        src = pslots[sl.src]
        here = tuple(src[2].value)
        old = poly_at(src[6].raw, here, here)
        if sl.src == spec.capital_slot and spec.capital_raw:
            continue
        if tuple(sl.pos) != here or sl.src == spec.capital_slot:
            # its old outline now carries whichever region's id the split gave that land
            res = uncarve_footprint(CellView(g), old, pid if inside(*here) else ppid)
            print(f"   footprint {sl.src}: old outline merged back into land ({len(res['cells'])} cells)")
        else:
            taken.append(old)

    def place(xy, label, raw, anchor, min_land, parent=False):
        view = CellView(g)
        make = lambda x, y: poly_at(raw, anchor, (x, y))
        skip = []
        while True:      # a site whose carve snaps into a degenerate record is rolled back
            x, y, outline = find_outline_site(view, make, xy, ppid if parent else pid,
                                              p_inside if parent else inside, min_land=min_land, taken=taken,
                                              avoid=avoid, avoid_ring=frozenset(), allow_run=True, skip=skip)
            r0, c0 = g.cell_of(x, y)
            bad = self_test(view, [(r, c) for r in range(r0 - 4, r0 + 5) for c in range(c0 - 4, c0 + 5)])
            assert not bad, f"{spec.name} {label}: encoding rules do not reproduce cells near {(x, y)}: {bad[:3]}"
            snap = g.snapshot()
            res = carve_footprint(view, outline, ppid if parent else pid, allow_run=True)
            bad = check_cells(view, res["cells"] + sorted(ring_of(res["cells"]))) + check_nodes(view, res["cells"])
            if not bad:
                break
            assert len(skip) < 20, f"{spec.name} {label}: carved cells break a vanilla invariant: {bad[:3]}"
            g.restore(snap)
            view = CellView(g)
            skip.append((x, y))
            print(f"   site ({x:.3f}, {y:.3f}) for {label} rejected: {bad[0]}")
        taken.append(outline)
        print(f"   carved footprint {label}: {xy} -> ({x:.3f}, {y:.3f}), outline area {res['area']:.3f} over "
              f"{len(res['cells'])} cells, {res['vertices_added']} vertices added")
        return (x, y)

    old_cap = spec.capital
    if not spec.capital_raw:
        raw, anchor = template_outline(regions_root, spec, "capital")
        spec.capital = place(old_cap, "capital", raw, anchor, 0.7)
    ddx, ddy = spec.capital[0] - old_cap[0], spec.capital[1] - old_cap[1]
    for sl in spec.slots:
        if sl.kind.startswith("settlement_"):
            sl.pos = (sl.pos[0] + ddx, sl.pos[1] + ddy)
    for sl in spec.moved:
        src = pslots[sl.src]
        if tuple(sl.pos) != tuple(src[2].value):
            sl.pos = place(sl.pos, sl.key, src[6].raw, tuple(src[2].value), 0.5, parent=sl.stay)
    for sl in spec.slots:
        if sl.kind in ("town", "resource"):
            raw, anchor = template_outline(regions_root, spec, sl.kind)
            sl.pos = place(sl.pos, sl.key, raw, anchor, 0.5)
    print(f"footprint {spec.name}: capital {old_cap} -> {spec.capital} (carved, path id {pid})")


def renumber_startpos_nodes(sp_root, gi: int, grid: AreaGrid) -> int:
    """OBSTACLE_BASE_GRID_NODE[0] is the cell's position in the grid's flat sequence
    (7.10); inserting footprint records shifts every later cell."""
    seq = grid.sequence_index()
    osys = ObstacleSystem(find(sp_root, "CAMPAIGN_PATHFINDER").children[0].children[gi])
    changed = 0
    for packed, ni in osys.pairs:
        want = seq[(packed >> 16) * grid.cols + (packed & 0xFFFF)]
        node = osys.nodes.children[ni]
        if node[0].value != want:
            set_int(node[0], want)
            changed += 1
    return changed


class GridClones:
    """Obstacle state of one PATHFINDING_GRID across every region cloned into it. The
    cells are rebuilt once over all of them (ObstacleCloner.finish), so a cell shared
    by obstacles of two neighbouring new regions keeps both, with the donor's
    combined-overlap entries."""

    def __init__(self, sp_root, donor_root, grid: int):
        self.dst = ObstacleSystem(find(sp_root, "CAMPAIGN_PATHFINDER").children[0].children[grid])
        self.src = ObstacleSystem(find(donor_root, "CAMPAIGN_PATHFINDER").children[0].children[grid])
        self.cloner = ObstacleCloner(self.dst, self.src)

    def finish(self, label: str) -> None:
        st = self.cloner.finish()
        dst = self.dst
        print(f"   {label}: {st['cells']} cells rebuilt ({st['new_nodes']} new nodes, "
              f"{st['merged_into_existing']} merged into existing nodes); "
              f"{st['entries_dropped']} donor entries dropped (un-cloned donor obstacles/characters), "
              f"{st['slot_refs_dropped']} obstacle slot refs dropped; manager +{st['manager_added']} -> "
              f"{len(dst.manager.children)}; {len(dst.nodes.children)} nodes, {len(dst.entries)} entries, "
              f"{len(dst.forts.children)} fort obstacles")


def add_fort_obstacles(sp_root, gc: GridClones, regions_root, spec: RegionSpec, ids: IdPool,
                       owner_id: int, region_item, region_ai_id: int, mode: str = "full") -> None:
    """Clone every donor fort obstacle that falls inside this region, plus the synthetic
    parked fort object each one needs. The id fix-up is GridClones.finish()."""
    inside = region_ring_inside(regions_root, spec.name)
    dst, src = gc.dst, gc.src
    donor_ids = donor_obstacles_in(src, inside)
    if not donor_ids:
        print(f"fort obstacles: donor has none inside {spec.name} -- settlement may be a trap cell")
        return
    fort_ary = region_item.children[35]
    cai_forts = find(sp_root, "CAI_WORLD_FORTS")
    theatre_ids = [34]
    for item in find(sp_root, "CAI_WORLD_REGIONS").children:
        cr_ = find(item, "CAI_REGION")
        if cr_.children[10].value == spec.name:
            theatre_ids = list(cr_.children[0].value)
            break

    # Template forts come from VANILLA (our own startpos), never from the donor: the
    # donor's synthetic forts carry an empty name key and a Lord-only localisation
    # string, and the engine looks the name up in a map that is empty in vanilla,
    # which crashed campaign load at Empire.exe+0x6a7a16.
    vanilla_forts = []
    for it_ in find(sp_root, "REGIONS_ARRAY").children:
        R_ = find(it_, "REGION")
        for f_ in R_.children[35].children:
            vanilla_forts.append(f_)
    if not vanilla_forts:
        print("   WARNING: no vanilla fort to use as a template; skipping fort objects")
    vanilla_cai = {}
    for item in (cai_forts.children if cai_forts else []):
        cf = find(item, "CAI_FORT")
        if cf is not None:
            vanilla_cai[cf.children[0].value] = item

    made = []
    for n, sid in enumerate(donor_ids):
        nid = ids.obj()
        stats = gc.cloner.clone(sid, nid)
        made.append((sid, nid, stats))
        cx, cy = stats["centre"]
        if mode == "geometry" or not vanilla_forts:
            continue
        tpl = vanilla_forts[n % len(vanilla_forts)]
        tpl_oid = tpl[1].children[1].value
        f = deep_copy(tpl)
        set_int(f[0], owner_id)
        sgr = f[1]
        set_int(find(sgr, "GARRISON_RESIDENCE").children[0], owner_id)
        set_int(sgr.children[1], nid)
        # The fort OBJECT is parked off-map exactly where URR parks its 70 synthetic
        # forts; only the obstacle sits on the settlement. A fort object at the
        # obstacle centre (first in-game obstacle test, 2026-10-02) put a real,
        # garrisonable fort on every town/slot: armies entered it and could not
        # leave, and pathing "teleported" through it (deep_dive 8.16).
        set_int(sgr.children[10], FORT_PARK[0])
        set_int(sgr.children[11], FORT_PARK[1])
        # SGR[12] is the fort's garrison ARMY id: the same value also indexes an
        # ARMY, its commander CHARACTER and a CAI_RESOURCE_MOBILE. Inheriting the
        # template's value made several forts claim one army, which crashed campaign
        # load. Two of the 15 vanilla forts carry 0 here, so "no garrison" is legal.
        set_int(sgr.children[12], 0)
        fort = f[2]
        set_int(fort.children[0], FORT_PARK[0])
        set_int(fort.children[1], FORT_PARK[1])
        set_int(fort.children[2], 0)          # URR synthetic forts: 0, real forts: 1
        set_int(fort.children[5], nid)
        set_int(fort.children[6], owner_id)
        fort_ary.children.append(f)
        # the AI's record for that fort, also cloned from the vanilla one
        src_cai = vanilla_cai.get(tpl_oid)
        if cai_forts is not None and src_cai is not None:
            c = deep_copy(src_cai)
            clear_bdi(c)
            set_int(find(c, "CAI_FORT").children[0], nid)
            ai_id = ids.cai()
            for j, ch in enumerate(c):
                if isinstance(ch, ESFPrimitive) and ch.type_tag in U4_FAMILY and j < 6:
                    set_int(ch, ai_id)
                    break
            sit = find(c, "CAI_SITUATED")
            if sit is not None:
                set_int(sit.children[0], FORT_PARK[0])
                set_int(sit.children[1], FORT_PARK[1])
                set_int(sit.children[2], region_ai_id)
                set_list(sit.children[3], theatre_ids)
            cai_forts.children.append(c)
        elif cai_forts is not None:
            print(f"   WARNING: no vanilla CAI_WORLD_FORTS record for template {tpl_oid}")
    print(f"fort obstacles: cloned {len(made)} from donor into {spec.name}; "
          f"forts {len(fort_ary.children)} in region, CAI_WORLD_FORTS now {len(cai_forts.children)}")
    for sid, nid, st in made:
        cx, cy = st["centre"]
        print(f"   {sid} -> {nid} at ({cx:.2f},{cy:.2f}) over {st['cells']} cells")
    print(f"   grid now: {len(dst.forts.children)} fort obstacles (cells rebuilt in GridClones.finish)")


# ─── DB pack ──────────────────────────────────────────────────────────────────

def ws(s: str) -> bytes:
    return struct.pack("<H", len(s)) + s.encode("utf-16-le")


def make_db(rows: list[bytes]) -> bytes:
    return b"\x01" + struct.pack("<I", len(rows)) + b"".join(rows)


PACK_TYPE_MOVIE = 4   # movie packs (like movies.pack, patch_media.pack) load without a user.script.txt entry


def build_pack(output: Path, files: list[tuple[str, bytes]], pack_type: int = PACK_TYPE_MOVIE) -> None:
    index = b"".join(struct.pack("<I", len(d)) + p.encode("ascii") + b"\x00" for p, d in files)
    header = struct.pack("<4sIIIII", b"PFH0", pack_type, 0, 0, len(files), len(index))
    output.write_bytes(header + index + b"".join(d for _, d in files))


def read_db_rows(path: Path, n_strings: int, n_u32: int) -> list[tuple]:
    """Rows of a simple ETW DB table: n_strings UTF-16 strings then n_u32 u32 per row."""
    b = path.read_bytes()
    n, p, rows = struct.unpack_from("<I", b, 1)[0], 5, []
    for _ in range(n):
        row = []
        for _ in range(n_strings):
            ln = struct.unpack_from("<H", b, p)[0]
            row.append(b[p + 2:p + 2 + 2 * ln].decode("utf-16-le"))
            p += 2 + 2 * ln
        row += struct.unpack_from(f"<{n_u32}I", b, p)
        p += 4 * n_u32
        rows.append(tuple(row))
    return rows


# ─── region overlay / minimap lookup ─────────────────────────────────────────

LOOKUP_VOID = (0, 63, 0)     # the dark green vanilla paints over land that is no region
# Grey-green vanilla paints over unexplorable's Atlantic islands (palette 152/153).
LOOKUP_NEUTRAL = ((158, 173, 156), (159, 174, 157))
# Areas add_region.py gave to an existing region: paint its neutral pixels in that
# region's colour (Madeira -> portugal).
LOOKUP_GIFTS = {"portugal": (96, 126, 89)}


def repaint_lookup(regions_root, specs, tga: Path, theatre_flag: int = 2) -> bytes | None:
    """Paint each new region's void-green pixels in <theatre>_lookup.tga with the region's
    palette colour. Vanilla paints Khwarezm almost entirely LOOKUP_VOID (3,553 of 3,922
    pixels inside the outline; 206 pixels carry Khiva's colour), so the minimap and
    overlay show no region there (2026-10-03). Raw in-place patch of the 8-bit
    colour-mapped TGA, so header, palette and orientation stay byte-identical."""
    d = bytearray(tga.read_bytes())
    idl, cmt, imt, cmfirst, cmlen, cmbits, xo, yo, w, h, bpp, desc = struct.unpack_from("<BBBHHBHHHHBB", d, 0)
    assert cmt == 1 and imt == 1 and bpp == 8 and cmbits == 24, "expected an uncompressed 8-bit colour-mapped TGA"
    pal = [tuple(d[18 + idl + 3 * i: 21 + idl + 3 * i][::-1]) for i in range(cmlen)]
    off = 18 + idl + cmlen * 3
    th = regions_root.children[0].children[theatre_flag][0]
    (x0, y0), (x1, y1) = th.children[1].value, th.children[2].value
    sx, sy = (x1 - x0) / w, (y1 - y0) / h
    void = pal.index(LOOKUP_VOID)
    pix_used = set(d[off:off + w * h])
    changed = 0
    for spec in specs:
        if spec.theatre_flag != theatre_flag:
            continue
        if spec.positions is not None and spec.parent is not None:
            assert tuple(spec.colour) not in pal, f"{spec.name}: colour {spec.colour} is already in the lookup palette"
        if tuple(spec.colour) not in pal and spec.positions is not None:
            # a new region's colour: claim a palette entry no pixel uses (vanilla
            # europe_lookup uses 150 of 256; the rest are spare black/white entries)
            i = next(i for i in reversed(range(cmlen)) if i not in pix_used and i != void)
            pal[i] = tuple(spec.colour)
            d[18 + idl + 3 * i: 21 + idl + 3 * i] = bytes(spec.colour[::-1])
            pix_used.add(i)
            print(f"lookup: {spec.name} colour {spec.colour} -> palette entry {i}")
        if tuple(spec.colour) not in pal:
            print(f"lookup: {spec.name} colour {spec.colour} not in the palette; not painted")
            continue
        idx = pal.index(tuple(spec.colour))
        inside = region_ring_inside(regions_root, spec.name)
        n = 0
        for y in range(h):                      # image row, top = y1
            row = (h - 1 - y) if not desc & 0x20 else y
            wy = y1 - (y + 0.5) * sy
            for x in range(w):
                o = off + row * w + x
                # a new region (explicit positions) owns every pixel inside its outline;
                # a reactivated one only the void-coloured ones
                if (spec.positions is not None or d[o] == void) and inside(x0 + (x + 0.5) * sx, wy):
                    d[o] = idx
                    n += 1
        if n:
            print(f"lookup: {spec.name} painted {n} void pixels")
        changed += n
    for name, colour in LOOKUP_GIFTS.items():
        names = [region_name(r) for r in regions_root.children[3].children[3].children]
        if name not in names or tuple(colour) not in pal:
            continue
        idx, neutral = pal.index(tuple(colour)), {i for i, c in enumerate(pal) if c in LOOKUP_NEUTRAL}
        inside = region_ring_inside(regions_root, name)
        n = 0
        for y in range(h):
            row = (h - 1 - y) if not desc & 0x20 else y
            wy = y1 - (y + 0.5) * sy
            for x in range(w):
                o = off + row * w + x
                if d[o] in neutral and inside(x0 + (x + 0.5) * sx, wy):
                    d[o] = idx
                    n += 1
        if n:
            print(f"lookup: {name} painted {n} neutral pixels (areas from add_region.py)")
        changed += n
    return bytes(d) if changed else None


def db_pack(specs: list[RegionSpec], out: Path, loc: bytes | None, tag: str = "new_regions",
            extra: list[tuple[str, bytes]] = ()) -> Path:
    regions = make_db([ws(s.name) + ws(s.continent) + b"".join(struct.pack("<I", c) for c in s.colour)
                       for s in specs])
    sett = make_db([ws(s.settlement_key) + ws(s.name) + ws(s.settlement_display)
                    + struct.pack("<I", s.settlement_tier) + ws("settlement") for s in specs])
    slots = make_db([ws(sl.key) + ws(s.name) + ws(sl.slot_type) + ws("") + ws("") + b"\x00"
                     for s in specs for sl in s.slots + [m for m in s.moved if m.key != m.src] if sl.kind == "resource"])
    towns = make_db([ws(sl.key) + ws(sl.slot_type) + ws(sl.display)
                     for s in specs for sl in s.slots + [m for m in s.moved if m.key != m.src] if sl.kind == "town"])
    files = [
        (f"db\\regions_tables\\{tag}_regions", regions),
        (f"db\\campaign_map_settlements_tables\\{tag}_settlements", sett),
        (f"db\\campaign_map_slots_tables\\{tag}_slots", slots),
        (f"db\\campaign_map_towns_and_ports_tables\\{tag}_towns", towns),
    ]
    if loc is not None:
        files.append(("text\\localisation.loc", loc))
    files += list(extra)
    p = out / f"{tag}.pack"
    build_pack(p, files)
    return p


# ─── localisation ─────────────────────────────────────────────────────────────

def pack_file(pack: Path, name: str) -> bytes:
    """One file out of a PFH0 pack."""
    b = pack.read_bytes()
    _, _, _, repsz, nfiles, isz = struct.unpack_from("<4sIIIII", b, 0)
    idx, p, off = b[24 + repsz:24 + repsz + isz], 0, 24 + repsz + isz
    for _ in range(nfiles):
        sz = struct.unpack_from("<I", idx, p)[0]
        e = idx.index(b"\0", p + 4)
        if idx[p + 4:e].decode("latin1").lower() == name.lower():
            return b[off:off + sz]
        p, off = e + 1, off + sz
    raise KeyError(f"{name} not in {pack}")


def loc_entries(specs: list[RegionSpec]) -> dict[str, str]:
    out = {}
    for s in specs:
        out[f"regions_onscreen_{s.name}"] = s.region_display
        out[f"start_pos_settlements_onscreen_name_{s.settlement_key}"] = s.settlement_display
        for sl in s.slots + [m for m in s.moved if m.display]:
            if sl.kind in ("town", "port"):
                out[f"campaign_map_towns_and_ports_onscreen_name_{sl.key}"] = sl.display
            elif sl.kind == "resource":
                out[f"campaign_map_slots_onscreen_{sl.key}"] = sl.display
    return out


def build_loc(vanilla: bytes, extra: dict[str, str]) -> bytes:
    """vanilla localisation.loc with our entries replaced/appended. Format:
    FF FE 'LOC' 00, u32 version, u32 count, then per entry a u16-length UTF-16 key,
    the same for the text, and a u8 tooltip flag."""
    assert vanilla[:6] == b"\xff\xfeLOC\x00", "not an ETW .loc"
    ver, n = struct.unpack_from("<II", vanilla, 6)
    p, entries = 14, []
    for _ in range(n):
        k_len = struct.unpack_from("<H", vanilla, p)[0]
        k = vanilla[p + 2:p + 2 + 2 * k_len].decode("utf-16-le")
        p += 2 + 2 * k_len
        t_len = struct.unpack_from("<H", vanilla, p)[0]
        t = vanilla[p + 2:p + 2 + 2 * t_len].decode("utf-16-le")
        p += 2 + 2 * t_len
        entries.append([k, t, vanilla[p]])
        p += 1
    assert p == len(vanilla)
    pos = {e[0]: i for i, e in enumerate(entries)}
    for k, t in extra.items():
        if k in pos:
            entries[pos[k]][1] = t
        else:
            entries.append([k, t, 0])
    body = b"".join(ws(k) + ws(t) + bytes([f]) for k, t, f in entries)
    return vanilla[:6] + struct.pack("<II", ver, len(entries)) + body


# ─── driver ───────────────────────────────────────────────────────────────────

def roundtrip_ok(path: Path) -> bool:
    r = ESFReader(path)
    return ESFWriter(r.read_root(), r.tag_names, r.timestamp).to_bytes() == path.read_bytes()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions", nargs="+", default=list(SPECS), choices=sorted(SPECS),
                    help="regions to reactivate (default: all eight); drop some to bisect a crash")
    ap.add_argument("--regions-esf", type=Path, default=Path("data/gc/regions.esf"))
    ap.add_argument("--pathfinding-esf", type=Path, default=Path("data/gc/pathfinding.esf"))
    ap.add_argument("--startpos-esf", type=Path, default=Path("data/campaigns/main/startpos_vanilla.esf"))
    ap.add_argument("--donor-regions-esf", type=Path, default=Path("data/lord_mod/lord_regions.esf"),
                    help="URR regions.esf: source of capital and slot positions")
    ap.add_argument("--vanilla-regions-db", type=Path,
                    default=Path("data/lord_mod/vanilla_db/regions_tables/regions"))
    ap.add_argument("--loc-pack", type=Path, default=Path("../data/patch_en.pack"),
                    help="pack holding the vanilla text/localisation.loc to extend")
    ap.add_argument("--no-loc", action="store_true", help="ship no localisation (names show as keys)")
    ap.add_argument("--europe-lookup", type=Path, default=Path("data/gc/europe_lookup.tga"),
                    help="vanilla europe_lookup.tga; new europe-theatre regions are painted over its void pixels")
    ap.add_argument("--out", type=Path, default=Path("out/new_regions"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-transfers", action="store_true",
                    help="keep vanilla owners of labrador / new_mexico (see TRANSFERS)")
    ap.add_argument("--footprints", action="store_true",
                    help="transplant slot footprints into pathfinding.esf (writes out/pathfinding.esf); "
                         "requires --obstacles none")
    ap.add_argument("--obstacles", choices=["none", "geometry", "full"], default="full",
                    help="bisect switch: none = no obstacles at all; geometry = obstacle "
                         "records/nodes only, no fort objects or CAI fort records; full = both")
    ap.add_argument("--no-footprint", nargs="+", default=[], choices=sorted(SPECS), metavar="REGION",
                    help="bisect switch: build these regions without a settlement/slot footprint")
    a = ap.parse_args()
    if a.list:
        for s in SPECS.values():
            print(f"{s.name:26} {s.region_display:14} {s.settlement_display:22} "
                  f"owner {s.owner_faction:12} template {s.template}")
        return
    specs = [SPECS[n] for n in a.regions]
    for spec in specs:
        if spec.name in a.no_footprint:
            spec.footprints = False
    out = a.out
    out.mkdir(parents=True, exist_ok=True)

    rr = ESFReader(a.regions_esf)
    regions_root = rr.read_root()
    if a.footprints and a.obstacles != "none":
        sys.exit("--footprints edits cells that cloned obstacle entries describe; use --obstacles none")
    pfr = ESFReader(a.pathfinding_esf)
    pf_root = pfr.read_root()
    donor_regions_root = ESFReader(a.donor_regions_esf).read_root()
    continent = {r[0]: r[1] for r in read_db_rows(a.vanilla_regions_db, 2, 3)}
    for spec in specs:
        resolve_spec(spec, regions_root, donor_regions_root, continent[spec.template])
        print(f"== {spec.name} '{spec.region_display}' capital {spec.settlement_display} "
              f"(template {spec.template}, owner {spec.owner_faction}, grid {spec.pf_grid}) ==")
        check_geometry(regions_root, pf_root, spec)

    sr = ESFReader(a.startpos_esf)
    sp_root = sr.read_root()
    ids = IdPool(all_ints(sp_root))
    fp_grids = {}
    if a.footprints:
        fp_grids = place_footprints(specs, pf_root, sp_root, regions_root)
        for spec in specs:
            check_geometry(regions_root, pf_root, spec)
    donor_path = Path(specs[0].obstacle_donor)
    donor_root = None
    if a.obstacles != "none" and donor_path.exists():
        donor_root = ESFReader(donor_path).read_root()
    grids: dict[int, GridClones] = {}
    cwr = find(sp_root, "CAI_WORLD_REGIONS")
    for spec in specs:
        print(f"\n== {spec.name} ==")
        cai_idx = next(i for i, it in enumerate(cwr.children)
                       if find(it, "CAI_REGION").children[10].value == spec.name)
        cr = find(cwr.children[cai_idx], "CAI_REGION")
        cai_region_ai_id = cwr.children[cai_idx][2].value
        cai_pos = (cr.children[4].value, cr.children[5].value)
        print(f"CAI_WORLD_REGIONS[{cai_idx}] ai id {cai_region_ai_id}, pos {cai_pos}")
        patch_regions_esf(regions_root, spec, cai_pos)
        los = spec.los_bbox      # None -> minimal [False] LINE_OF_SIGHT
        if spec.parent is not None:
            take_parent_share(sp_root, spec)
        owner_id, region_item = patch_startpos(sp_root, spec, cai_idx, cai_region_ai_id, cai_pos, ids, los)
        if spec.parent is not None:
            transfer_startpos(sp_root, spec, cai_region_ai_id, region_item)
        if donor_root is None:
            why = "--obstacles none" if a.obstacles == "none" else f"no donor {donor_path}"
            print(f"fort obstacles: SKIPPED ({why})")
            continue
        if spec.pf_grid not in grids:
            grids[spec.pf_grid] = GridClones(sp_root, donor_root, spec.pf_grid)
        add_fort_obstacles(sp_root, grids[spec.pf_grid], regions_root, spec, ids,
                           owner_id, region_item, cai_region_ai_id, mode=a.obstacles)
    patch_trade_network(sp_root, [s.name for s in specs])
    patch_trade_routes(sp_root)
    resituate(sp_root, regions_root, specs)
    built = {s.name for s in specs}
    for region, owner, gov_like, cap_to, trigger in TRANSFERS:
        if trigger in built and not a.no_transfers:
            transfer_region(sp_root, region, owner, gov_like, cap_to)
    for g, gc in sorted(grids.items()):
        gc.finish(f"grid {g}")

    for gi, g in sorted(fp_grids.items()):
        g.serialize()
        print(f"pathfinding grid {gi}: startpos node sequence ids renumbered: {renumber_startpos_nodes(sp_root, gi, g)}")
    if fp_grids:
        p_pf = out / "pathfinding.esf"
        p_pf.write_bytes(ESFWriter(pf_root, pfr.tag_names, pfr.timestamp).to_bytes())
        print(f"wrote {p_pf} ({p_pf.stat().st_size:,} B), round-trip", "OK" if roundtrip_ok(p_pf) else "FAIL")

    loc = None
    if not a.no_loc:
        loc = build_loc(pack_file(a.loc_pack, "text\\localisation.loc"), loc_entries(specs))
    p_reg = out / "regions.esf"
    p_sp = out / "startpos.esf"
    p_reg.write_bytes(ESFWriter(regions_root, rr.tag_names, rr.timestamp).to_bytes())
    p_sp.write_bytes(ESFWriter(sp_root, sr.tag_names, sr.timestamp).to_bytes())
    extra = []
    lookup = repaint_lookup(regions_root, specs, a.europe_lookup) if a.europe_lookup.exists() else None
    if lookup is not None:
        extra.append(("campaign_maps\\global_map\\europe_lookup.tga", lookup))
    p_pack = db_pack(specs, out, loc, extra=extra)
    print(f"\nwrote {p_reg} ({p_reg.stat().st_size:,} B), "
          f"{p_sp} ({p_sp.stat().st_size:,} B), {p_pack} ({p_pack.stat().st_size:,} B)")
    print("round-trip:", "regions OK" if roundtrip_ok(p_reg) else "regions FAIL",
          "|", "startpos OK" if roundtrip_ok(p_sp) else "startpos FAIL")
    (out / "MANIFEST.txt").write_text(
        f"regions={' '.join(s.name for s in specs)}\nobstacles={a.obstacles}\n"
        f"footprints={' '.join(f'{s.name}:{s.footprints}' for s in specs) if a.footprints else 'none'}\n"
        f"regions.esf -> data/campaign_maps/global_map/regions.esf\n"
        f"startpos.esf -> data/campaigns/main/startpos.esf\n"
        f"{p_pack.name} -> data/{p_pack.name}  (movie pack, auto-loads; DB rows"
        f"{'' if loc is None else ' + text/localisation.loc'})\n"
        + ("pathfinding.esf -> data/campaign_maps/global_map/pathfinding.esf\n" if fp_grids
           else "pathfinding.esf: NOT modified (leave the vanilla file in place)\n"))


if __name__ == "__main__":
    main()
