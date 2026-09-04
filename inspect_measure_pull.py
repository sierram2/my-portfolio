"""
inspect_measure_pull.py — one-off diagnostic, not part of the app.

Walks through the exact same steps get_cancer_dashboard_data() uses to
pull one cancer-type measure (default: 48, "Age-adjusted Incidence Rate
of Bladder Cancer... per 100,000 Population"), printing what each
intermediate step returns. This tells us definitively WHERE the empty
result comes from: no stratification level, no TX counties listed for
this measure/geo-type, or an empty API response despite counties
existing.

Run:
    python inspect_measure_pull.py
    python inspect_measure_pull.py 967   # try a different measure_id
"""
import sys

from CDC_Review import (
    _get_api_token, _get_stratification_level_id, _list_texas_counties,
    _fetch_county_data, _api_get,
)

measure_id = int(sys.argv[1]) if len(sys.argv) > 1 else 48
year = "2021"

token = _get_api_token()
print(f"Testing measure_id={measure_id}, year={year}\n")

# Step 1: stratification level (county geography, geo_type_id=2)
try:
    strat_id = _get_stratification_level_id(token, measure_id, geo_type_id=2)
    print(f"[1] Stratification level id (county): {strat_id}")
except Exception as e:
    print(f"[1] FAILED: {type(e).__name__}: {e}")
    print("    -> This measure may not support county-level (geo_type_id=2) stratification at all.")
    strat_id = None

# Also check what geo types ARE available, for comparison
print("\n[1b] Raw stratification level responses by geo_type_id:")
for geo_type_id, geo_label in [(1, "state"), (2, "county"), (3, "census tract"), (4, "zip")]:
    try:
        raw = _api_get(token, f"stratificationlevel/{measure_id}/{geo_type_id}/0")
        print(f"    geo_type_id={geo_type_id} ({geo_label}): {len(raw)} stratification level(s) -> {raw}")
    except Exception as e:
        print(f"    geo_type_id={geo_type_id} ({geo_label}): FAILED - {type(e).__name__}: {e}")

# Step 2: list of TX counties for this measure
try:
    counties = _list_texas_counties(token, measure_id, geo_type_id=2)
    print(f"\n[2] TX counties found for this measure: {len(counties)}")
    if counties:
        print(f"    First 5: {counties[:5]}")
except Exception as e:
    print(f"\n[2] FAILED: {type(e).__name__}: {e}")
    counties = []

# Step 3: actual data fetch, if we got counties and a strat level
if counties and strat_id is not None:
    try:
        df = _fetch_county_data(token, measure_id, counties, [year], strat_id)
        print(f"\n[3] Data rows returned: {len(df)}")
        if not df.empty:
            print(df.head(10).to_string())
    except Exception as e:
        print(f"\n[3] FAILED: {type(e).__name__}: {e}")
else:
    print("\n[3] SKIPPED — no counties and/or no stratification level from steps 1-2.")

print("\n---")
print("If [1b] shows 0 stratification levels for geo_type_id=2 (county) but a")
print("non-zero count for geo_type_id=1 (state), that confirms this measure only")
print("publishes STATE-level data, not county-level — which is why every pull")
print("comes back empty and the trend chart can't build a per-county map. In that")
print("case the fix is to pull these measures at the state level directly instead")
print("of trying to average county-level values.")