"""
build_zip_crosswalk.py — one-time local build script, not part of the app.

Builds static/tx_zip_county.json: a { "78701": "48453", ... } mapping from
every Texas zip code to the same 5-digit county FIPS ("geoId") already used
by tx_counties.geojson / the choropleth map. The cancer_report page's zip
search reads this file client-side to look up which county a zip falls in.

Requires: pip install uszipcode

Run once from your project root:
    python build_zip_crosswalk.py

Safe to re-run any time (e.g. if zip data changes) — it just overwrites the
output file.
"""
import json
import os
import re

from uszipcode import SearchEngine

HERE = os.path.dirname(os.path.abspath(__file__))
GEOJSON_PATH = os.path.join(HERE, "static", "tx_counties.geojson")
OUTPUT_PATH = os.path.join(HERE, "static", "tx_zip_county.json")


def _normalize_county_name(name):
    """'Harris County' / 'Harris' / 'HARRIS' -> 'harris' for matching."""
    s = re.sub(r"\s*county\s*$", "", str(name).strip(), flags=re.IGNORECASE)
    return s.strip().lower()


def load_name_to_fips():
    """Build {normalized_county_name: fips} from the same geojson the
    choropleth map already uses, so the crosswalk is guaranteed
    consistent with the map's FIPS codes."""
    with open(GEOJSON_PATH, "r") as f:
        geojson = json.load(f)

    name_to_fips = {}
    sample_props = None
    for feature in geojson["features"]:
        props = feature.get("properties", {})
        if sample_props is None:
            sample_props = props  # for the debug print below

        fips = props.get("geoId")
        # Try the common property names census/TIGER county geojson exports
        # use for the human-readable name. If none of these match your
        # file, check the printed sample properties below and adjust this
        # list.
        name = (
            props.get("NAME") or props.get("name") or
            props.get("COUNTY") or props.get("county_name") or
            props.get("NAMELSAD")
        )
        if fips and name:
            name_to_fips[_normalize_county_name(name)] = str(fips)

    if not name_to_fips:
        print("WARNING: couldn't find a usable name property in the geojson.")
        print("Sample properties from the first feature, to help you fix the list above:")
        print(json.dumps(sample_props, indent=2))

    return name_to_fips


def main():
    name_to_fips = load_name_to_fips()
    print(f"Loaded {len(name_to_fips)} county name -> FIPS mappings from {GEOJSON_PATH}")

    search = SearchEngine()
    zip_to_fips = {}
    unmatched_counties = set()

    # by_state is the current uszipcode API; older versions used
    # by_state_and_type or similar — adjust here if this errors on your
    # installed version.
    results = search.by_state(state="TX", returns=0)  # returns=0 -> all results
    print(f"uszipcode returned {len(results)} TX zip records")

    for r in results:
        if not r.zipcode or not r.county:
            continue
        key = _normalize_county_name(r.county)
        fips = name_to_fips.get(key)
        if fips:
            zip_to_fips[r.zipcode] = fips
        else:
            unmatched_counties.add(r.county)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(zip_to_fips, f)

    print(f"\nWrote {len(zip_to_fips)} zip -> FIPS mappings to {OUTPUT_PATH}")
    if unmatched_counties:
        print(f"\n{len(unmatched_counties)} county names from uszipcode didn't match the geojson:")
        for c in sorted(unmatched_counties):
            print(f"  - {c}")
        print("If this list is non-empty, the geojson's name property may be")
        print("formatted differently than expected — check the WARNING above (if any)")
        print("and adjust _normalize_county_name / the property list in load_name_to_fips().")


if __name__ == "__main__":
    main()