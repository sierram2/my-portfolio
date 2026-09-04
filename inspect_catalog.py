"""
inspect_catalog.py — one-off diagnostic, not part of the app.

Run this locally (same folder as CDC_Review.py, same token setup) once
the CDC EPHTN API is back from maintenance, to see every catalog entry
that looks cancer-related and whether the current filter logic would
keep or exclude it — and critically, what indicatorName/contentAreaName
actually contain, since we've been guessing at those.
Delete this file once we've confirmed the fix.

    python inspect_catalog.py
"""
from CDC_Review import _get_api_token, _fetch_catalog, _discover_cancer_type_measures

token = _get_api_token()
catalog = _fetch_catalog(token)
print(f"Total catalog entries: {len(catalog)}")
print(f"Columns available: {list(catalog.columns)}\n")

measure_name = catalog.get("measureName", "").fillna("").astype(str)
indicator_name = catalog.get("indicatorName", "").fillna("").astype(str)
content_area = catalog.get("contentAreaName", "").fillna("").astype(str)

searchable = (measure_name + " " + indicator_name + " " + content_area).str.lower()
cancer_rows = catalog[searchable.str.contains("cancer|leukemia|melanoma|lymphoma|hodgkin", regex=True)]

print(f"All {len(cancer_rows)} catalog entries that look cancer-related:\n")
for _, row in cancer_rows.iterrows():
    mid = row.get("measureId")
    mname = row.get("measureName")
    iname = row.get("indicatorName")
    caname = row.get("contentAreaName")
    print(f"  [{mid}]")
    print(f"     measureName:      {mname}")
    print(f"     indicatorName:    {iname}")
    print(f"     contentAreaName:  {caname}")
    print()

print("---\n")

kept = _discover_cancer_type_measures(catalog)
print(f"After current filter logic, {len(kept)} candidates would be kept:\n")
for c in kept:
    print(f"  [{c['measure_id']}] {c['label']}")