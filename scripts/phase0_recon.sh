#!/usr/bin/env bash
# =============================================================================
# Phase 0 Reconnaissance — run this the moment you have game files
#
# Edit DATA_ROOT below to point at your extracted ETW pack files.
# =============================================================================
set -euo pipefail

DATA_ROOT="${ETW_DATA_ROOT:-./data}"
GC="${DATA_ROOT}/gc"
WP="${DATA_ROOT}/warpath"
OUT="./out"

ETWNG="${PWD}/tools/etwng/esfxml"

if [[ ! -d "$ETWNG" ]]; then
    echo "[ERROR] tools/etwng not found. Run setup_dev.sh first."
    exit 1
fi

echo "=== PHASE 0: ETW PATHFINDING COMPILER RECONNAISSANCE ==="
echo "Data root: ${DATA_ROOT}"
echo ""

# ── Inventory ────────────────────────────────────────────────────────────────
echo "--- File inventory ---"
find "${GC}"  -name "*.esf" -o -name "*.tga" 2>/dev/null | sort | tee esf_inventory_gc.txt
find "${WP}"  -name "*.esf" -o -name "*.tga" 2>/dev/null | sort | tee esf_inventory_wp.txt
echo ""

# ── ESF dumps ────────────────────────────────────────────────────────────────
echo "--- Dumping ESF files to XML ---"

for FILE in pathfinding regions; do
    if [[ -f "${GC}/${FILE}.esf" ]]; then
        mkdir -p "${OUT}/gc_${FILE}"
        ruby "${ETWNG}/esf2xml" "${GC}/${FILE}.esf" "${OUT}/gc_${FILE}/"
        echo "[OK] Grand Campaign ${FILE}.esf → ${OUT}/gc_${FILE}/"
    else
        echo "[SKIP] ${GC}/${FILE}.esf not found"
    fi

    if [[ -f "${WP}/${FILE}.esf" ]]; then
        mkdir -p "${OUT}/wp_${FILE}"
        ruby "${ETWNG}/esf2xml" "${WP}/${FILE}.esf" "${OUT}/wp_${FILE}/"
        echo "[OK] Warpath ${FILE}.esf → ${OUT}/wp_${FILE}/"
    else
        echo "[SKIP] ${WP}/${FILE}.esf not found"
    fi
done

# ── West Pommerania grep — THE critical question ──────────────────────────────
echo ""
echo "=== CRITICAL: West Pommerania in pathfinding.esf? ==="
GC_PF="${OUT}/gc_pathfinding/esf.xml"

if [[ -f "$GC_PF" ]]; then
    echo "--- Searching regions.esf ---"
    grep -i "pommerania\|pomerania\|pomern" "${OUT}/gc_regions/esf.xml" \
        && echo "[FOUND in regions.esf — outline data exists]" \
        || echo "[NOT FOUND in regions.esf]"

    echo ""
    echo "--- Searching pathfinding.esf ---"
    grep -i "pommerania\|pomerania\|pomern" "$GC_PF" \
        && echo "[FOUND in pathfinding.esf — REACTIVATION PATH (Method A) AVAILABLE]" \
        || echo "[NOT FOUND in pathfinding.esf — full compiler required (Method B)]"
else
    echo "[SKIP] gc_pathfinding/esf.xml not yet generated"
fi

# ── Also check for other potential dormant regions ────────────────────────────
echo ""
echo "=== Searching for other dormant European regions ==="
CANDIDATES=("silesia" "lorraine" "wallachia" "dalmatia" "palatinate" "mecklenburg")
for TERM in "${CANDIDATES[@]}"; do
    echo -n "  ${TERM}: "
    grep -ic "$TERM" "${GC_PF}" 2>/dev/null && echo "(found in pathfinding)" || echo "(not found)"
done

# ── Top-level structure ───────────────────────────────────────────────────────
echo ""
echo "=== Top-level pathfinding.esf structure (first 100 lines) ==="
head -100 "${GC_PF}" 2>/dev/null || echo "[Run ESF dump first]"

echo ""
echo "=== Warpath diff (first 200 lines) ==="
diff "${OUT}/gc_pathfinding/esf.xml" "${OUT}/wp_pathfinding/esf.xml" \
    > warpath_pathfinding_diff.txt 2>/dev/null \
    && head -200 warpath_pathfinding_diff.txt \
    || echo "[Cannot diff — one or both files missing]"

echo ""
echo "=== Phase 0 complete. Review findings in: ==="
echo "  esf_inventory_gc.txt"
echo "  out/gc_pathfinding/esf.xml"
echo "  out/gc_regions/esf.xml"
echo "  warpath_pathfinding_diff.txt"
echo ""
echo "Document findings in: docs/reverse_eng/phase0_findings.md"
