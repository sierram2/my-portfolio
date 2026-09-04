import pandas as pd
import requests

from CDC_Review import _get_api_token, BASE

token = _get_api_token()
tests = [
    (1095, 2, "48001"),  # known county-level measure
    (48, 1, "48"),       # cancer-type measure
]

for measure_id, geo_type, geo_id in tests:
    print(f"\nMEASURE {measure_id}")

    levels = requests.get(
        f"{BASE}/stratificationlevel/{measure_id}/{geo_type}/0",
        params={"apiToken": token},
        timeout=90,
    ).json()

    for level in levels:
        strat_id = level["id"]
        print(f"stratification={strat_id} ({level['name']})")

        body = {
            "geographicTypeIdFilter": str(geo_type),
            "geographicItemsFilter": geo_id,
            "temporalTypeIdFilter": "1",
            "temporalItemsFilter": "2015,2016,2017,2018,2019,2020,2021,2022,2023",
        }

        response = requests.post(
            f"{BASE}/getCoreHolder/{measure_id}/{strat_id}/0/0",
            params={"apiToken": token},
            json=body,
            timeout=120,
        )

        payload = response.json()
        rows = payload.get("tableResult", [])
        print(f"status={response.status_code}, rows={len(rows)}")

        if rows:
            print(pd.DataFrame(rows).head().to_string(index=False))