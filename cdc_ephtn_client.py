"""
cdc_ephtn_client.py

Shared low-level client for the CDC Environmental Public Health Tracking
Network (EPHTN) API — the generic "pull a measure for Texas counties/state,
handle the API's error envelope, load the county geojson, build a
choropleth" plumbing used by both CDC_Review.py (cancer) and
CDC_Heart_Review.py (heart disease & stroke). Content-specific logic
(which measures to pull, how to rank/discover them, insight/methodology
text) stays in each dashboard's own module.
"""
import json
import os
import time

import pandas as pd
import requests
import plotly.graph_objects as go
import plotly.io as pio

BASE = "https://ephtracking.cdc.gov/apigateway/api/v1"
TX_FIPS = "48"


def get_api_token():
    """Render: read from env var. Local dev: fall back to a gitignored file."""
    token = os.environ.get("EPHTN_API_TOKEN")
    if token:
        return token
    local_path = os.path.join(os.getcwd(), "CDC_API_TOKEN.txt")
    if os.path.exists(local_path):
        with open(local_path, "r") as f:
            return f.read().strip()
    raise RuntimeError(
        "No CDC API token found. Set the EPHTN_API_TOKEN environment variable "
        "(Render) or create a local 'CDC_API_TOKEN.txt' file (gitignored) "
        "containing just the token string."
    )


def check_api_error(payload):
    """The CDC EPHTN API returns errors (rate limiting included) as a
    normal HTTP 200 response whose JSON body carries the real status in
    a "code"/"errorTypeId" envelope — e.g. {"code": 429, "message":
    "User has requested too many queries for their time frame", ...}.
    r.raise_for_status() never sees this, since the HTTP status is 200.
    Left unchecked, the caller instead sees an empty/malformed payload
    and raises a misleading downstream error that hides the real cause."""
    if isinstance(payload, dict) and "errorTypeId" in payload:
        raise RuntimeError(
            f"CDC API error {payload.get('code')}: {payload.get('message', 'Unknown error')}"
        )


def api_get(token, path, **params):
    params["apiToken"] = token
    r = requests.get(f"{BASE}/{path}", params=params, timeout=90)
    r.raise_for_status()
    payload = r.json()
    check_api_error(payload)
    return payload


def to_fips(geo_id, state_fips=TX_FIPS):
    """Normalize whatever the API gives us in 'geoId' into a 5-digit
    state+county FIPS code matching the GeoJSON's 'geoId' property."""
    s = str(geo_id).split(".")[0].strip()
    if len(s) <= 3:
        return f"{state_fips}{s.zfill(3)}"
    return s.zfill(5)


def get_stratification_level_id(token, measure_id, geo_type_id=2, is_smoothed=0):
    levels = api_get(
        token,
        f"stratificationlevel/{measure_id}/{geo_type_id}/{is_smoothed}",
    )

    if isinstance(levels, dict):
        levels = (
            levels.get("data")
            or levels.get("result")
            or levels.get("tableResult")
            or []
        )

    if not isinstance(levels, list):
        raise RuntimeError(
            f"Unexpected stratification response for measure {measure_id}: "
            f"{type(levels).__name__}"
        )

    levels = [level for level in levels if isinstance(level, dict)]

    if not levels:
        raise RuntimeError(
            f"No valid stratification levels for measure {measure_id}, "
            f"geography type {geo_type_id}."
        )

    plain = [level for level in levels if not level.get("stratificationType")]
    chosen = plain[0] if plain else levels[0]
    return chosen["id"]


def list_texas_counties(token, measure_id, geo_type_id=2):
    items = api_get(token, f"geographicItems/{measure_id}/{geo_type_id}/0")

    if isinstance(items, dict):
        items = items.get("data") or items.get("result") or []

    if not isinstance(items, list):
        return []

    return [
        str(item["childGeographicId"])
        for item in items
        if isinstance(item, dict)
        and str(item.get("parentGeographicId")) == TX_FIPS
        and item.get("childGeographicId") is not None
    ]


def fetch_county_data(token, measure_id, county_fips, years, strat_level_id,
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
    check_api_error(data)
    df = pd.DataFrame(data.get("tableResult", []))
    if df.empty:
        return df
    # Cell-size / deidentification suppression: drop flagged rows so we
    # never show a value built from too few underlying cases.
    df = df[df["suppressionFlag"].astype(str) != "1"]
    df["dataValue"] = pd.to_numeric(df["dataValue"], errors="coerce")
    df["temporalId"] = pd.to_numeric(df["temporalId"], errors="coerce")
    return df[["geoId", "geo", "temporalId", "dataValue"]]


def fetch_state_data(token, measure_id, years, strat_level_id):
    url = f"{BASE}/getCoreHolder/{measure_id}/{strat_level_id}/0/0"
    body = {
        "geographicTypeIdFilter": "1",
        "geographicItemsFilter": TX_FIPS,
        "temporalTypeIdFilter": "1",
        "temporalItemsFilter": ",".join(years),
    }

    response = requests.post(
        url,
        params={"apiToken": token},
        json=body,
        headers={"Accept": "application/json"},
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    check_api_error(data)

    df = pd.DataFrame(data.get("tableResult", []))
    if df.empty:
        return df

    if "suppressionFlag" in df.columns:
        df = df[df["suppressionFlag"].astype(str) != "1"]

    df["dataValue"] = pd.to_numeric(df["dataValue"], errors="coerce")
    df["temporalId"] = pd.to_numeric(df["temporalId"], errors="coerce")
    df = df.dropna(subset=["temporalId", "dataValue"])

    return df[["geoId", "geo", "temporalId", "dataValue"]]


def pull_state_measure_df(token, measure_id, years):
    strat_id = get_stratification_level_id(token, measure_id, geo_type_id=1)
    return fetch_state_data(token, measure_id, years, strat_id)


def pull_measure_df(token, measure_id, years):
    """Full county-year dataframe for a single measureId."""
    strat_id = get_stratification_level_id(token, measure_id)
    counties = list_texas_counties(token, measure_id)
    if not counties:
        return pd.DataFrame()
    return fetch_county_data(token, measure_id, counties, years, strat_id)


def with_retry(pull_fn, *args, retries=2, backoff=1.5):
    """Call pull_fn(*args), retrying once on failure. Running several
    requests concurrently makes occasional transient failures / rate-limit
    responses from the CDC API more likely — a bare single attempt
    silently drops that candidate entirely."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return pull_fn(*args)
        except Exception as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise last_exc


def fetch_catalog(token):
    """Fetch the full EPHTN measure catalog."""
    catalog_data = api_get(token, "measuresearch")
    if not isinstance(catalog_data, list):
        return pd.DataFrame()
    return pd.DataFrame(catalog_data)


def load_tx_geojson():
    """Loads the Texas county boundaries file from static/tx_counties.geojson
    relative to the project root (i.e. next to this file)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "static", "tx_counties.geojson")
    with open(path, "r") as f:
        return json.load(f)


def build_choropleth(county_values, title, colorbar_title, value_suffix="", hover_label=None):
    """Generic Texas county choropleth. county_values is
    {fips: {"name": ..., "value": ...}}."""
    geojson = load_tx_geojson()
    fips_codes = list(county_values.keys())
    values = [county_values[f]["value"] for f in fips_codes]
    names = [county_values[f]["name"] for f in fips_codes]
    hover_label = hover_label or colorbar_title

    # Per-county border color/width as arrays (not a single string) so the
    # frontend can highlight one searched county via Plotly.restyle without
    # rebuilding the whole figure.
    border_colors = ["#ffffff"] * len(fips_codes)
    border_widths = [0.75] * len(fips_codes)

    fig = go.Figure(go.Choropleth(
        geojson=geojson,
        featureidkey="properties.geoId",
        locations=fips_codes,
        z=values,
        text=names,
        colorscale=[[0, "#ffffff"], [1, "#000000"]],
        marker_line_color=border_colors,
        marker_line_width=border_widths,
        colorbar_title=colorbar_title,
        hovertemplate=f"<b>%{{text}}</b><br>{hover_label}: %{{z}}{value_suffix}<extra></extra>",
    ))
    fig.update_geos(fitbounds="locations", visible=False)
    fig.update_layout(
        title=title,
        margin=dict(l=0, r=0, t=40, b=0),
        height=560,
    )
    return pio.to_json(fig)
