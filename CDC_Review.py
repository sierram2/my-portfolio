"""
CDC_Review.py

Live data layer for the Cancer Data Analysis page (/blog/cancer-analysis).
Pulls Texas county-level cancer prevalence + PM2.5 + water quality data
from the CDC Environmental Public Health Tracking Network (EPHTN) API,
and returns plain JSON-ready dicts for Chart.js to render on the front end.

Mirrors the pattern used by analytics/ga_daily.py: fetch -> shape into JSON
-> hand to a Flask route, which either renders a template or returns raw JSON.
"""
import os
import time

import numpy as np
import pandas as pd
import requests

from scipy.stats import pearsonr

BASE = "https://ephtracking.cdc.gov/apigateway/api/v1"
TX_FIPS = "48"

MEASURE_CONFIG = {
    "cancer_prevalence": {
        "id": 1095, "temporal_type": 1,
        "years": [str(y) for y in range(2015, 2022)], "static": False,
    },
    "pm25_annual_avg": {
        "id": 296, "temporal_type": 1,
        "years": [str(y) for y in range(2015, 2022)], "static": False,
    },
    "water_quality_index": {
        "id": 1201, "temporal_type": 2,
        "years": ["2010"], "static": True,  # single 2006-2010 snapshot
    },
}

# Simple in-memory cache so we don't re-hit the CDC API on every page view.
# The CDC API is slow (multiple calls per measure) and rate-limit friendly
# use matters more than sub-hour freshness for a report like this.
_CACHE = {"data": None, "fetched_at": 0}
_CACHE_TTL_SECONDS = 60 * 60  # 1 hour


def _get_api_token():
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


def _api_get(token, path, **params):
    params["apiToken"] = token
    r = requests.get(f"{BASE}/{path}", params=params, timeout=90)
    r.raise_for_status()
    return r.json()


def _get_stratification_level_id(token, measure_id, geo_type_id=2, is_smoothed=0):
    levels = _api_get(token, f"stratificationlevel/{measure_id}/{geo_type_id}/{is_smoothed}")
    plain = [l for l in levels if not l.get("stratificationType")]
    chosen = plain[0] if plain else levels[0]
    return chosen["id"]


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
    df = df[df["suppressionFlag"].astype(str) != "1"]
    df["dataValue"] = pd.to_numeric(df["dataValue"], errors="coerce")
    df["temporalId"] = pd.to_numeric(df["temporalId"], errors="coerce")
    return df[["geoId", "geo", "temporalId", "dataValue"]]


def _pull_measure(token, label, cfg):
    measure_id = cfg["id"]
    strat_id = _get_stratification_level_id(token, measure_id)
    counties = _list_texas_counties(token, measure_id)
    if not counties:
        raise RuntimeError(f"No Texas counties found for measureId={measure_id} ('{label}').")
    df = _fetch_county_data(token, measure_id, counties, cfg["years"], strat_id,
                             temporal_type_id=cfg["temporal_type"])
    names = df[["geoId", "geo"]].drop_duplicates() if "geo" in df.columns else None
    df = df.rename(columns={"dataValue": label})
    if "geo" in df.columns:
        df = df.drop(columns=["geo"])
    if cfg["static"]:
        df = df.drop(columns=["temporalId"])
    return df, names


def _build_merged_dataframe(token):
    annual_dfs, static_dfs = [], []
    county_names = None
    for label, cfg in MEASURE_CONFIG.items():
        df, names = _pull_measure(token, label, cfg)
        if names is not None:
            county_names = names
        (static_dfs if cfg["static"] else annual_dfs).append(df)
        time.sleep(0.3)  # be polite to the API

    merged = annual_dfs[0]
    for df in annual_dfs[1:]:
        merged = pd.merge(merged, df, on=["geoId", "temporalId"], how="inner")
    for df in static_dfs:
        merged = pd.merge(merged, df, on=["geoId"], how="inner")
    if county_names is not None:
        merged = merged.merge(county_names, on="geoId", how="left")
    return merged


def _quartiles(values):
    q1, med, q3 = np.percentile(values, [25, 50, 75])
    return {"min": float(np.min(values)), "q1": float(q1), "median": float(med),
            "q3": float(q3), "max": float(np.max(values))}


def _linear_fit(x, y):
    slope, intercept = np.polyfit(x, y, 1)
    xs = np.linspace(np.min(x), np.max(x), 30)
    ys = slope * xs + intercept
    return xs.tolist(), ys.tolist()


def _shape_for_frontend(merged):
    value_cols = list(MEASURE_CONFIG.keys())

    # Yearly average bar chart
    yearly = (merged.groupby("temporalId")["cancer_prevalence"]
              .mean().reset_index().sort_values("temporalId"))
    yearly_prevalence = {
        "years": yearly["temporalId"].astype(int).tolist(),
        "values": [round(v, 2) for v in yearly["cancer_prevalence"].tolist()],
    }

    # Quartile range ("boxplot") by year
    years_sorted = sorted(merged["temporalId"].dropna().unique())
    boxplot_by_year = {"years": [int(y) for y in years_sorted], "quartiles": []}
    for y in years_sorted:
        vals = merged.loc[merged["temporalId"] == y, "cancer_prevalence"].dropna().values
        boxplot_by_year["quartiles"].append(_quartiles(vals) if len(vals) else None)

    # Top / bottom counties, most recent year
    latest_year = merged["temporalId"].dropna().max()
    latest = merged.loc[merged["temporalId"] == latest_year, ["geo", "cancer_prevalence"]].dropna()
    latest = latest.sort_values("cancer_prevalence", ascending=False)
    top_counties = latest.head(15).to_dict(orient="records")
    bottom_counties = latest.tail(15).sort_values("cancer_prevalence").to_dict(orient="records")

    # Distributions (histogram bins computed server-side)
    distributions = {}
    for col in value_cols:
        vals = merged[col].dropna().values
        if len(vals) == 0:
            continue
        counts, edges = np.histogram(vals, bins=20)
        distributions[col] = {
            "bin_labels": [f"{edges[i]:.1f}-{edges[i+1]:.1f}" for i in range(len(edges) - 1)],
            "counts": counts.tolist(),
        }

    # Statewide dual trend: cancer_prevalence vs pm25_annual_avg
    trend = (merged.groupby("temporalId")[["cancer_prevalence", "pm25_annual_avg"]]
             .mean().reset_index().sort_values("temporalId"))
    trend_dual = {
        "years": trend["temporalId"].astype(int).tolist(),
        "cancer_prevalence": [round(v, 2) for v in trend["cancer_prevalence"].tolist()],
        "pm25_annual_avg": [round(v, 2) for v in trend["pm25_annual_avg"].tolist()],
    }

    # Correlations + scatter + regression line + residuals
    correlations = {}
    scatter = {}
    residuals = {}
    for col in value_cols[1:]:
        sub = merged[["cancer_prevalence", col]].dropna()
        if len(sub) < 3:
            continue
        r, p = pearsonr(sub["cancer_prevalence"], sub[col])
        correlations[col] = {"r": round(float(r), 3), "p": round(float(p), 4), "n": int(len(sub))}

        x, y = sub[col].values, sub["cancer_prevalence"].values
        line_x, line_y = _linear_fit(x, y)
        scatter[col] = {
            "points": [{"x": float(a), "y": float(b)} for a, b in zip(x, y)],
            "fit_line": [{"x": a, "y": b} for a, b in zip(line_x, line_y)],
        }

        slope, intercept = np.polyfit(x, y, 1)
        predicted = slope * x + intercept
        resid = y - predicted
        residuals[col] = [{"x": float(a), "y": float(b)} for a, b in zip(x, resid)]

    return {
        "generated_at": int(time.time()),
        "yearly_prevalence": yearly_prevalence,
        "boxplot_by_year": boxplot_by_year,
        "top_counties": top_counties,
        "bottom_counties": bottom_counties,
        "distributions": distributions,
        "trend_dual": trend_dual,
        "correlations": correlations,
        "scatter": scatter,
        "residuals": residuals,
        "latest_year": int(latest_year),
        "caveat": (
            "County-level (ecological) correlations, not individual-level evidence. "
            "Age distribution, population size, and reporting lags aren't controlled for. "
            "water_quality_index reflects a single 2006-2010 snapshot applied to all years."
        ),
    }


def get_cancer_dashboard_data(force_refresh=False):
    """Public entry point used by app.py. Cached for _CACHE_TTL_SECONDS so the
    live CDC API isn't re-queried on every page view."""
    now = time.time()
    if not force_refresh and _CACHE["data"] is not None and (now - _CACHE["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _CACHE["data"]

    token = _get_api_token()
    merged = _build_merged_dataframe(token)
    shaped = _shape_for_frontend(merged)

    _CACHE["data"] = shaped
    _CACHE["fetched_at"] = now
    return shaped