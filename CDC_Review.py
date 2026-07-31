"""
CDC_Review.py

Live data layer for the Cancer Data Analysis page (/blog/cancer-analysis).

Pulls two things from the CDC Environmental Public Health Tracking
Network (EPHTN) API:

  1. County-level "all sites" crude cancer prevalence for the most
     recent year -> rendered as a Plotly choropleth map.
  2. Statewide yearly trends for the five most-tracked site-specific
     cancer types (lung, breast, prostate, colorectal, melanoma) ->
     rendered as a multi-line Chart.js chart.

The site-specific measure IDs are NOT hardcoded. EPHTN's /measuresearch
catalog is queried live and filtered by keyword, so this keeps working
even if CDC renumbers measures — see _discover_top5_measure_ids().
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import plotly.graph_objects as go
import plotly.io as pio

BASE = "https://ephtracking.cdc.gov/apigateway/api/v1"
TX_FIPS = "48"

ALL_SITES_MEASURE_ID = 1095       # "Cancer, all sites, crude rate"
YEARS = [str(y) for y in range(2015, 2022)]

# Keyword -> display label for the five cancer types CDC's own sub-county
# cancer data pilot covers (lung, breast, prostate, colorectal, melanoma).
# We search the live measure catalog for each keyword rather than
# hardcoding measureIds, since those can differ/change over time.
TOP5_CANCER_KEYWORDS = {
    "Lung & Bronchus": ["lung"],
    "Female Breast": ["breast"],
    "Prostate": ["prostate"],
    "Colorectal": ["colorectal", "colon"],
    "Melanoma": ["melanoma"],
}

# In-memory cache so the CDC API isn't re-queried on every page view.
_CACHE = {"data": None, "fetched_at": 0}
_CACHE_TTL_SECONDS = 60 * 60  # 1 hour


def _get_api_token():
    """Render: read from env var. Local dev: fall back to a gitignored file."""
    token = os.environ.get("EPHTN_API_TOKEN")

    if token:
        return token.strip()

    token_file = Path(__file__).with_name("CDC_API_TOKEN.txt")

    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()

    raise RuntimeError(
        "EPHTN_API_TOKEN is not configured and CDC_API_TOKEN.txt was not found."
    )


def _api_get(token, path, **params):
    params["apiToken"] = token

    response = requests.get(
        f"{BASE}/{path}",
        params=params,
        timeout=90
    )

    response.raise_for_status()
    return response.json()


def _to_fips(geo_id, state_fips=TX_FIPS):
    """Normalize whatever the API gives us in 'geoId' into a 5-digit
    state+county FIPS code matching the GeoJSON's 'geoId' property."""
    s = str(geo_id).split(".")[0].strip()
    if len(s) <= 3:
        return f"{state_fips}{s.zfill(3)}"
    return s.zfill(5)


def _get_stratification_level_id(
    token,
    measure_id,
    geo_type_id=2,
    is_smoothed=0
):
    response = _api_get(
        token,
        f"stratificationlevel/{measure_id}/{geo_type_id}/{is_smoothed}"
    )

    if isinstance(response, dict):
        levels = (
            response.get("data")
            or response.get("results")
            or response.get("items")
            or response.get("stratificationLevels")
            or []
        )
    elif isinstance(response, list):
        levels = response
    else:
        levels = []

    if not levels:
        raise RuntimeError(
            f"CDC returned no stratification levels for "
            f"measureId={measure_id}, geoTypeId={geo_type_id}, "
            f"isSmoothed={is_smoothed}. Response: {response!r}"
        )

    plain = [
        level for level in levels
        if str(
            level.get(
                "isSmoothed",
                level.get("smoothed", 0)
            )
        ).lower() in {"0", "false"}
    ]

    chosen = plain[0] if plain else levels[0]

    stratification_id = (
        chosen.get("stratificationLevelId")
        or chosen.get("stratificationlevelId")
        or chosen.get("id")
    )

    if stratification_id is None:
        raise RuntimeError(
            f"CDC stratification response has no ID: {chosen!r}"
        )

    return stratification_id


def _list_texas_counties(token, measure_id, geo_type_id=2):
    items = _api_get(token, f"geographicItems/{measure_id}/{geo_type_id}/0")
    return [
        str(i["childGeographicId"])
        for i in items
        if str(i.get("parentGeographicId")) == TX_FIPS
    ]


def _fetch_county_data(token, measure_id, county_fips, years, strat_level_id,
                        geo_type_id=2, temporal_type_id=1):
    url = f"{BASE}/getCoreHolder/{measure_id}/{strat_level_id}/0/0"
    body = {
        "geographicTypeIdFilter": str(geo_type_id),
        "geographicItemsFilter": ",".join(county_fips),
        "temporalTypeIdFilter": str(temporal_type_id),
        "temporalItemsFilter": ",".join(years),
    }
    r = requests.post(url, params={"apiToken": token}, json=body,
                       headers={"Accept": "application/json"}, timeout=120)
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data.get("tableResult", []))
    if df.empty:
        return df
    # Cell-size / deidentification suppression: drop flagged rows so we
    # never show a value built from too few underlying cases.
    df = df[df["suppressionFlag"].astype(str) != "1"]
    df["dataValue"] = pd.to_numeric(df["dataValue"], errors="coerce")
    df["temporalId"] = pd.to_numeric(df["temporalId"], errors="coerce")
    return df[["geoId", "geo", "temporalId", "dataValue"]]


def _pull_measure_df(token, measure_id, years):
    """Full county-year dataframe for a single measureId."""
    strat_id = _get_stratification_level_id(token, measure_id)
    counties = _list_texas_counties(token, measure_id)
    if not counties:
        return pd.DataFrame()
    return _fetch_county_data(token, measure_id, counties, years, strat_id)


def _discover_top5_measure_ids(token):
    """Search the live EPHTN measure catalog for the five site-specific
    cancer measures we want, instead of hardcoding measureIds. Prefers a
    "crude rate" variant when multiple matches exist for a keyword."""
    catalog = pd.DataFrame(_api_get(token, "measuresearch"))
    searchable = (
        catalog.get("measureName", pd.Series(dtype=str)).fillna("") + " " +
        catalog.get("indicatorName", pd.Series(dtype=str)).fillna("")
    ).str.lower()

    resolved = {}
    for label, keywords in TOP5_CANCER_KEYWORDS.items():
        mask = False
        for kw in keywords:
            mask = mask | searchable.str.contains(kw.lower())
        matches = catalog[mask]
        if matches.empty:
            continue
        crude = matches[matches["measureName"].str.contains("crude", case=False, na=False)]
        chosen = crude.iloc[0] if len(crude) else matches.iloc[0]
        resolved[label] = int(chosen["measureId"])
    return resolved


def _load_tx_geojson():
    """Loads the Texas county boundaries file. Assumes it lives at
    static/tx_counties.geojson relative to the project root (i.e. next
    to this file) — adjust the path here if you move it."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "static", "tx_counties.geojson")
    with open(path, "r") as f:
        import json
        return json.load(f)


def _build_choropleth(county_values, latest_year):
    geojson = _load_tx_geojson()
    fips_codes = list(county_values.keys())
    values = [county_values[f]["value"] for f in fips_codes]
    names = [county_values[f]["name"] for f in fips_codes]

    fig = go.Figure(go.Choropleth(
        geojson=geojson,
        featureidkey="properties.geoId",
        locations=fips_codes,
        z=values,
        text=names,
        colorscale="Reds",
        marker_line_color="white",
        marker_line_width=0.5,
        colorbar_title="Crude Prevalence (%)",
        hovertemplate="<b>%{text}</b><br>Prevalence: %{z}%<extra></extra>",
    ))
    fig.update_geos(fitbounds="locations", visible=False)
    fig.update_layout(
        title=f"Cancer Prevalence by County — {latest_year}",
        margin=dict(l=0, r=0, t=40, b=0),
        height=560,
    )
    return pio.to_json(fig)


def _build_insights(county_values, trend_series, latest_year, debug):
    """Five data-driven takeaways, computed from whatever actually came
    back this pull — not static/hardcoded text."""
    insights = []

    if county_values:
        by_value = sorted(county_values.items(), key=lambda kv: kv[1]["value"])
        lowest = by_value[0][1]
        highest = by_value[-1][1]
        insights.append(
            f"{highest['name']} had the highest reported crude cancer prevalence in "
            f"{latest_year} at {highest['value']}%, compared to {lowest['name']} at "
            f"{lowest['value']}%."
        )

    if trend_series:
        deltas = {}
        for label, series in trend_series.items():
            vals = [v for v in series["values"] if v is not None]
            if len(vals) >= 2:
                deltas[label] = vals[-1] - vals[0]
        if deltas:
            fastest = max(deltas, key=lambda k: deltas[k])
            slowest = min(deltas, key=lambda k: deltas[k])
            insights.append(
                f"Among the five tracked cancer types, {fastest} rose the most over the "
                f"period tracked ({deltas[fastest]:+.2f} percentage points)."
            )
            insights.append(
                f"{slowest} showed the smallest change over the same period "
                f"({deltas[slowest]:+.2f} percentage points), "
                + ("suggesting a decline." if deltas[slowest] < 0 else "roughly holding steady.")
            )

    if debug.get("counties_matched") is not None:
        missing = 254 - debug["counties_matched"]
        if missing > 0:
            insights.append(
                f"{missing} of Texas's 254 counties had no reportable {latest_year} data due "
                f"to cell-size suppression (too few underlying cases to protect privacy) — "
                f"gaps by design, not missing data."
            )

    if trend_series:
        first_label = next(iter(trend_series))
        years = trend_series[first_label]["years"]
        if years:
            insights.append(
                f"Trends span {years[0]}–{years[-1]}; rural, low-population counties are "
                f"more likely to be suppressed in any given year than urban ones, since "
                f"suppression is based on case counts, not population size."
            )

    return insights[:5]


def _shape_for_frontend(all_sites_df, top5_dfs):
    latest_year = int(all_sites_df["temporalId"].dropna().max())
    latest = all_sites_df[all_sites_df["temporalId"] == latest_year].dropna(subset=["dataValue"])

    county_values = {
        _to_fips(row.geoId): {"name": row.geo, "value": round(float(row.dataValue), 2)}
        for row in latest.itertuples()
    }

    map_fig_json = _build_choropleth(county_values, latest_year)

    # Top-5 cancer trend series: statewide yearly average per cancer type
    trend_series = {}
    for label, df in top5_dfs.items():
        if df.empty:
            continue
        yearly = df.groupby("temporalId")["dataValue"].mean().reset_index().sort_values("temporalId")
        trend_series[label] = {
            "years": yearly["temporalId"].astype(int).tolist(),
            "values": [round(v, 2) for v in yearly["dataValue"].tolist()],
        }

    debug = {
        "total_rows_all_sites": int(len(all_sites_df)),
        "rows_for_latest_year": int(len(latest)),
        "counties_matched": len(county_values),
        "top5_measures_found": list(trend_series.keys()),
    }

    insights = _build_insights(county_values, trend_series, latest_year, debug)

    return {
        "generated_at": int(time.time()),
        "latest_year": latest_year,
        "map_fig_json": map_fig_json,
        "trend_series": trend_series,
        "insights": insights,
        "debug": debug,
        "caveat": (
            "County-level (ecological) data, not individual-level evidence. "
            "Cell-size suppression removes any county-year built from too few "
            "underlying cases to protect privacy."
        ),
    }


def get_cancer_dashboard_data(force_refresh=False):
    """Public entry point used by app.py. Cached for _CACHE_TTL_SECONDS so
    the live CDC API isn't re-queried on every page view."""
    now = time.time()
    if not force_refresh and _CACHE["data"] is not None and (now - _CACHE["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _CACHE["data"]

    token = _get_api_token()

    all_sites_df = _pull_measure_df(token, ALL_SITES_MEASURE_ID, YEARS)
    if all_sites_df.empty:
        raise RuntimeError("CDC API returned no usable (non-suppressed) all-sites rows for Texas.")

    top5_ids = _discover_top5_measure_ids(token)
    top5_dfs = {}
    for label, measure_id in top5_ids.items():
        try:
            top5_dfs[label] = _pull_measure_df(token, measure_id, YEARS)
        except requests.HTTPError:
            # Some site-specific measures may not have Texas county data at
            # all (too rare, or not tracked at this geography) — skip rather
            # than fail the whole page.
            continue
        time.sleep(0.2)

    shaped = _shape_for_frontend(all_sites_df, top5_dfs)
    _CACHE["data"] = shaped
    _CACHE["fetched_at"] = now
    return shaped