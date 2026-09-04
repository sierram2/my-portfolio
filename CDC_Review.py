"""
CDC_Review.py

Live data layer for the Cancer Data Analysis page (/blog/cancer-analysis).

Pulls two things from the CDC Environmental Public Health Tracking
Network (EPHTN) API:

  1. County-level "all sites" crude cancer prevalence for the most
     recent year -> rendered as a Plotly choropleth map.
  2. Statewide yearly trends for the five cancer types with the HIGHEST
     latest-year statewide prevalence -> rendered as a multi-line
     Chart.js chart. Which five types those are is determined by the
     data itself each pull, not a fixed guess-list — see
     _discover_cancer_type_measures() and _rank_top5_by_latest_year().
"""
import os
import re
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import requests
import plotly.graph_objects as go
import plotly.io as pio


BASE = "https://ephtracking.cdc.gov/apigateway/api/v1"
TX_FIPS = "48"

ALL_SITES_MEASURE_ID = 1095       # "Crude Prevalence of Cancer among Adults (Model-based)"
TOTAL_INCIDENCE_MEASURE_ID = 1458  # "Age-adjusted Incidence Rate of Total Cancers per 100,000" — statewide only
YEARS = [str(y) for y in range(2015, 2022)]
TOP_N_CANCER_TYPES = 5

# In-memory cache so the CDC API isn't re-queried on every page view.
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


def _check_api_error(payload):
    """The CDC EPHTN API returns errors (rate limiting included) as a
    normal HTTP 200 response whose JSON body carries the real status in
    a "code"/"errorTypeId" envelope — e.g. {"code": 429, "message":
    "User has requested too many queries for their time frame", ...}.
    r.raise_for_status() never sees this, since the HTTP status is 200.
    Left unchecked, the caller instead sees an empty/malformed payload
    and raises a misleading downstream error (e.g. "No valid
    stratification levels for measure X") that hides the real cause and
    gives _with_retry's short backoff no chance of being long enough."""
    if isinstance(payload, dict) and "errorTypeId" in payload:
        raise RuntimeError(
            f"CDC API error {payload.get('code')}: {payload.get('message', 'Unknown error')}"
        )


def _api_get(token, path, **params):
    params["apiToken"] = token
    r = requests.get(f"{BASE}/{path}", params=params, timeout=90)
    r.raise_for_status()
    payload = r.json()
    _check_api_error(payload)
    return payload


def _to_fips(geo_id, state_fips=TX_FIPS):
    """Normalize whatever the API gives us in 'geoId' into a 5-digit
    state+county FIPS code matching the GeoJSON's 'geoId' property."""
    s = str(geo_id).split(".")[0].strip()
    if len(s) <= 3:
        return f"{state_fips}{s.zfill(3)}"
    return s.zfill(5)


def _get_stratification_level_id(token, measure_id, geo_type_id=2, is_smoothed=0):
    levels = _api_get(
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


def _list_texas_counties(token, measure_id, geo_type_id=2):
    items = _api_get(token, f"geographicItems/{measure_id}/{geo_type_id}/0")

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
    _check_api_error(data)
    df = pd.DataFrame(data.get("tableResult", []))
    if df.empty:
        return df
    # Cell-size / deidentification suppression: drop flagged rows so we
    # never show a value built from too few underlying cases.
    df = df[df["suppressionFlag"].astype(str) != "1"]
    df["dataValue"] = pd.to_numeric(df["dataValue"], errors="coerce")
    df["temporalId"] = pd.to_numeric(df["temporalId"], errors="coerce")
    return df[["geoId", "geo", "temporalId", "dataValue"]]
def _fetch_state_data(token, measure_id, years, strat_level_id):
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
    _check_api_error(data)

    df = pd.DataFrame(data.get("tableResult", []))
    if df.empty:
        return df

    if "suppressionFlag" in df.columns:
        df = df[df["suppressionFlag"].astype(str) != "1"]

    df["dataValue"] = pd.to_numeric(df["dataValue"], errors="coerce")
    df["temporalId"] = pd.to_numeric(df["temporalId"], errors="coerce")
    df = df.dropna(subset=["temporalId", "dataValue"])

    return df[["geoId", "geo", "temporalId", "dataValue"]]


def _pull_state_measure_df(token, measure_id, years):
    strat_id = _get_stratification_level_id(
        token,
        measure_id,
        geo_type_id=1,
    )
    return _fetch_state_data(token, measure_id, years, strat_id)

def _pull_measure_df(token, measure_id, years):
    """Full county-year dataframe for a single measureId."""
    strat_id = _get_stratification_level_id(token, measure_id)
    counties = _list_texas_counties(token, measure_id)
    if not counties:
        return pd.DataFrame()
    return _fetch_county_data(token, measure_id, counties, years, strat_id)


def _with_retry(pull_fn, *args, retries=2, backoff=1.5):
    """Call pull_fn(*args), retrying once on failure. Running several
    requests concurrently (see _rank_top5_by_latest_year and
    get_cancer_dashboard_data) is much faster, but it also makes
    occasional transient failures / rate-limit responses from the CDC
    API more likely — a bare single attempt silently drops that
    candidate entirely, which is what caused the top-5 line chart and
    the takeaways that depend on it to go empty."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return pull_fn(*args)
        except Exception as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise last_exc


def _clean_measure_label(name):
    """'Cancer, Female Breast, Crude Rate' -> 'Female Breast'"""
    s = re.sub(r"^cancer,?\s*", "", name, flags=re.IGNORECASE)
    s = re.sub(r",?\s*(crude|age-adjusted)\s*rate$", "", s, flags=re.IGNORECASE)
    return s.strip().strip(",").strip()


def _fetch_catalog(token):
    """Fetch the full EPHTN measure catalog ONCE. Both cancer-type and
    smoking discovery reuse this instead of each re-fetching it — that
    alone was one of the bigger avoidable delays on a cache miss."""
    catalog_data = _api_get(token, "measuresearch")
    if not isinstance(catalog_data, list):
        return pd.DataFrame()
    return pd.DataFrame(catalog_data)


def _discover_cancer_type_measures(catalog, exclude_measure_id=ALL_SITES_MEASURE_ID):
    """Search the measure catalog for every site-specific cancer
    incidence rate measure (lung, breast, colorectal, melanoma, leukemia,
    etc.) — whatever CDC currently tracks, not a fixed list. We rank
    these by actual prevalence later rather than assuming which five
    matter most.

    Filter logic below is grounded in an actual live catalog pull
    (869 entries, confirmed via a one-off diagnostic script), not
    guesswork. Two things confirmed directly from that data:

    1. contentAreaName IS a reliable signal here: real cancer-type
       incidence measures have contentAreaName == "Cancer", while the
       screening/behavioral measures that were causing empty pulls
       (colorectal screening, cervical screening, mammography use) sit
       under contentAreaName == "Health Status" with
       indicatorName == "Screenings & Prevention Practices" — a
       completely different bucket, not a fuzzy-text-match problem.
    2. Each cancer type has MULTIPLE measure variants in the catalog:
       an annual per-100k rate, a "5-year Period" / "3-year Period" /
       "10-year Period" aggregate, and separate sub-geography threshold
       variants ("5,000 Min. Population Area", "Census Tract", etc).
       We want exactly the plain annual county-level rate variant per
       type, not all of them.
    """
    if catalog.empty:
        return []

    def col(field):
        return catalog.get(field, pd.Series([""] * len(catalog))).fillna("").astype(str)

    content_area_lower = col("contentAreaName").str.lower().str.strip()
    indicator_name_lower = col("indicatorName").str.lower().str.strip()
    measure_name_lower = col("measureName").str.lower()

    is_cancer_content_area = content_area_lower == "cancer"
    is_incidence_indicator = indicator_name_lower.str.startswith("incidence of")
    is_total_cancers = indicator_name_lower == "incidence of total cancers"  # aggregate, like all-sites
    is_age_adjusted_rate = (
        measure_name_lower.str.contains("age-adjusted incidence rate")
        & measure_name_lower.str.contains("per 100,000")
    )
    is_period_or_subgeo_variant = measure_name_lower.str.contains(
        "5-year period|3-year period|10-year period|population area|census tract",
        regex=True,
    )

    base_mask = (
        is_cancer_content_area
        & is_incidence_indicator
        & ~is_total_cancers
        & is_age_adjusted_rate
        & ~is_period_or_subgeo_variant
    )

    candidates = catalog[base_mask].drop_duplicates(subset="measureId")

    out = []
    for _, row in candidates.iterrows():
        mid = int(row["measureId"])
        if mid == exclude_measure_id:
            continue
        # Prefer the indicatorName for the label — it's already clean
        # ("Incidence of Bladder Cancer" -> "Bladder Cancer"), whereas
        # measureName is a long descriptive sentence not meant for
        # display ("Age-adjusted Incidence Rate of Bladder Cancer
        # (including in situ) per 100,000 Population").
        label = re.sub(r"^incidence of\s*", "", str(row.get("indicatorName", "")), flags=re.IGNORECASE).strip()
        out.append({
            "measure_id": mid,
            "indicator_id": int(row["indicatorId"]),
            "label": label or _clean_measure_label(str(row.get("measureName", ""))),
        })
    return out


def _discover_annual_count_measures(catalog):
    """Map {indicatorId: measureId} for the plain 'Annual Number of Cases
    of X' variant of each cancer-type indicator — the raw yearly case
    count, as opposed to the age-adjusted per-100,000 rate that
    _discover_cancer_type_measures finds. Used to show both fields
    side by side in the measure comparison table.

    startswith("annual number of cases") (rather than .contains) is what
    excludes the "Average Annual Number of Cases ... over a 5-year
    Period" variant, which contains the same phrase but starts with
    "average" instead."""
    if catalog.empty:
        return {}

    def col(field):
        return catalog.get(field, pd.Series([""] * len(catalog))).fillna("").astype(str)

    content_area_lower = col("contentAreaName").str.lower().str.strip()
    indicator_name_lower = col("indicatorName").str.lower().str.strip()
    measure_name_lower = col("measureName").str.lower()

    mask = (
        (content_area_lower == "cancer")
        & indicator_name_lower.str.startswith("incidence of")
        & (indicator_name_lower != "incidence of total cancers")
        & measure_name_lower.str.startswith("annual number of cases")
    )

    out = {}
    for _, row in catalog[mask].iterrows():
        out[int(row["indicatorId"])] = int(row["measureId"])
    return out


def _discover_single_measure(catalog, keywords, exclude_terms=None):
    """Generic one-off search for a measure by keyword (used for the
    optional smoking-prevalence lookup below). Returns the first match,
    preferring a state-level variant with 'crude' or 'current' in the
    name, or None if nothing in the catalog matches.

    Deliberately does NOT search the catalog's free-text "keywords"
    field. That field tags measures with loosely-associated risk-factor
    terms (e.g. COPD hospitalization measures are tagged "tobacco,
    smoke" as context, since smoking causes COPD) — searching it caused
    a smoking-prevalence lookup to false-match "Crude Rate of
    Hospitalizations for COPD" and silently mislabel that chart.
    measureName/indicatorName/contentAreaName are curated fields that
    only mention a term when the measure is actually about it."""
    if catalog.empty:
        return None

    def col(field):
        return catalog.get(field, pd.Series([""] * len(catalog))).fillna("").astype(str)

    searchable = (col("measureName") + " " + col("indicatorName") + " " +
                  col("contentAreaName")).str.lower()

    mask = False
    for kw in keywords:
        mask = mask | searchable.str.contains(kw.lower())
    if exclude_terms:
        for term in exclude_terms:
            mask = mask & ~searchable.str.contains(term.lower())

    matches = catalog[mask]
    if matches.empty:
        return None
    # Build match_names from matches' own column, not the full-catalog
    # `col()` helper — mixing a full-catalog-indexed boolean Series into
    # a subset-indexed DataFrame triggers pandas' "Boolean Series key
    # will be reindexed to match DataFrame index" warning and can
    # silently select the wrong rows.
    match_names = (
        matches.get("measureName", pd.Series([""] * len(matches), index=matches.index))
        .fillna("")
        .astype(str)
        .str.lower()
    )

    # Caller always pulls the result via _pull_state_measure_df, but
    # several candidate measures (e.g. "Model-based; County/Census
    # Tract" smoking-prevalence variants) only publish county/tract-level
    # data and have no state-level stratification at all — pulling them
    # would just fail. Prefer an explicit "(State)" variant when one
    # exists in the matched set.
    state_level_names = match_names[match_names.str.contains("state")]
    if len(state_level_names):
        matches = matches.loc[state_level_names.index]
        match_names = state_level_names

    preferred_mask = match_names.str.contains("crude|current", regex=True)
    preferred = matches[preferred_mask]
    chosen = preferred.iloc[0] if len(preferred) else matches.iloc[0]
    return {"measure_id": int(chosen["measureId"]), "label": _clean_measure_label(str(chosen.get("measureName", "")))}


def _rank_top5_by_latest_year(
    token,
    candidates,
    latest_year,
    top_n=TOP_N_CANCER_TYPES,
    max_workers=4,
):
    """Rank cancer measures using each measure's latest available year."""

    def _rank_one(candidate):
        try:
            df = _with_retry(
                _pull_state_measure_df,
                token,
                candidate["measure_id"],
                YEARS,
            )
        except Exception:
            return None

        if df.empty:
            return None

        usable = df[df["temporalId"] <= latest_year]
        if usable.empty:
            return None

        ranking_year = int(usable["temporalId"].max())
        values = usable.loc[
            usable["temporalId"] == ranking_year,
            "dataValue",
        ]

        if values.empty or values.isna().all():
            return None

        return {
            **candidate,
            "latest_avg": round(float(values.mean()), 2),
            "ranking_year": ranking_year,
        }

    ranked = []
    failures = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_rank_one, c) for c in candidates]

        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                ranked.append(result)
            else:
                failures += 1

    ranked.sort(key=lambda item: item["latest_avg"], reverse=True)
    return ranked[:top_n], failures


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
                f"period tracked ({deltas[fastest]:+.2f} cases per 100,000 people, "
                "age-adjusted incidence)."
            )
            insights.append(
                f"{slowest} showed the smallest change over the same period "
                f"({deltas[slowest]:+.2f} cases per 100,000 people), "
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

    fallback_insights = [
        f"The dashboard includes reportable data for {len(county_values)} of 254 Texas counties.",
        f"The latest county data year is {latest_year}.",
        "County values are crude rates and are not adjusted for differences in age.",
        "CDC suppresses some values when case counts are too small to protect privacy.",
        "Statewide cancer-type trends are calculated separately from the county map.",
    ]

    for insight in fallback_insights:
        if len(insights) >= 5:
            break
        if insight not in insights:
            insights.append(insight)

    return insights[:5]


def _build_methodology(latest_year, top5_meta, debug, smoking_label=None):
    """Plain-language explanation of the data source, what's actually
    being measured, how the top-5 cancer types were chosen, and known
    limitations — filled in with the real numbers from this pull rather
    than static boilerplate."""
    type_list = ", ".join(m["label"] for m in top5_meta) if top5_meta else "the tracked cancer types"
    n_candidates = debug.get("cancer_type_candidates_considered", 0)

    return {
        "source": (
            "All figures on this page come from the CDC's Environmental Public Health "
            "Tracking Network (EPHTN) API, pulled live on each page load (cached for an "
            f"hour). The map and county rankings use measure {ALL_SITES_MEASURE_ID} "
            "('Crude Prevalence of Cancer among Adults'); the line chart pulls whichever "
            "site-specific cancer incidence measures the API currently has cataloged."
        ),
        "measure_definition": (
            "The map and county rankings show PREVALENCE, not incidence: a model-based "
            "estimate of the percentage of adults in each county who have EVER been "
            "diagnosed with any type of cancer (a lifetime history, self-reported in the "
            "underlying survey data and modeled down to the county level) — not new "
            "diagnoses in a given year. 'Crude' means it is not adjusted for age, so a "
            "county with an older population will tend to show a higher value than a "
            "younger one, independent of any environmental or behavioral factor. CDC also "
            "publishes an age-adjusted version of this same prevalence measure, but only "
            "at the county/census-tract level — not statewide — so it isn't shown here. "
            "The five-line trend chart and the 'Total, all types' row in the table below "
            "are a different kind of statistic: age-adjusted INCIDENCE rates — newly "
            "diagnosed cases per 100,000 people per year, drawn from actual cancer "
            "registry counts rather than a survey-based model. Prevalence and incidence "
            "numbers on this page are not directly comparable to each other."
        ),
        "top5_method": (
            f"The five lines shown were chosen by pulling every site-specific cancer "
            f"measure EPHTN currently tracks ({n_candidates} candidates considered), "
            f"computing each one's statewide average for {latest_year} (the most recent "
            f"year with data), and keeping the five with the highest average: "
            f"{type_list}. This ranking is recomputed on every data refresh, so the five "
            "types shown can change if the underlying data changes."
        ),
        "limitations": [
            "Ecological, not individual-level: these are county-level averages, not "
            "records tied to individual patients — they can suggest patterns but not "
            "prove causes.",
            "Cell-size suppression: CDC withholds any county-year built from too few "
            "underlying cases to protect patient privacy, so low-population counties "
            "are more likely to have gaps than high-population ones, in any given year.",
            "Crude, not age-adjusted — but only for the map: the county prevalence "
            "map and county rankings use a crude (non-age-adjusted) rate, so "
            "differences between counties can partly reflect age distribution rather "
            "than a real change in cancer risk. The five-line trend chart and the "
            "'Total, all types' figure are already age-adjusted incidence rates.",
            "Reporting lag: cancer registries take time to finalize data, so the most "
            "recent year or two shown may still be revised upward as more cases are "
            "reported.",
            (
                f"Smoking data: a matching measure ('{smoking_label}') was found and is "
                "shown for context, but EPHTN doesn't link individual smoking status to "
                "individual cancer cases — any relationship between the two charts on "
                "this page is suggestive at the population level only, not a tested link."
                if smoking_label else
                "No smoking-prevalence measure was found in EPHTN's current catalog for "
                "Texas — this dataset is primarily environmental/health tracking, not "
                "behavioral risk-factor survey data, so smoking context isn't available here."
            ),
        ],
    }


def _build_measure_table(trend_series, count_dfs, total_incidence_trend, state_average, all_sites_latest_year):
    """Side-by-side comparison of the different measure 'fields' EPHTN
    publishes for cancer, for the top-5 types plus the two statewide
    all-cancer figures — since a single age-adjusted rate (used for the
    map/ranking) doesn't show the raw case counts or how prevalence and
    incidence differ. Each row uses ITS OWN measure's latest available
    year, since annual-count and rate series don't always share one."""
    rows = []
    for label in trend_series:
        rate_series = trend_series[label]
        rate_year = rate_series["years"][-1] if rate_series["years"] else None
        rate_value = rate_series["values"][-1] if rate_series["values"] else None

        count_df = count_dfs.get(label)
        count_year = count_value = None
        if count_df is not None and not count_df.empty:
            latest_count_year = int(count_df["temporalId"].max())
            vals = count_df.loc[count_df["temporalId"] == latest_count_year, "dataValue"]
            if not vals.empty:
                count_year = latest_count_year
                count_value = int(round(vals.sum()))

        rows.append({
            "label": label,
            "rate_year": rate_year,
            "age_adjusted_rate": rate_value,
            "count_year": count_year,
            "annual_cases": count_value,
        })

    total_year = total_value = None
    if total_incidence_trend.get("years"):
        total_year = total_incidence_trend["years"][-1]
        total_value = total_incidence_trend["values"][-1]

    return {
        "rows": rows,
        "total_incidence": {"year": total_year, "age_adjusted_rate": total_value},
        "all_sites_crude_prevalence": {"year": all_sites_latest_year, "value": state_average},
    }


def _shape_for_frontend(all_sites_df, top5_dfs, top5_meta, cancer_type_candidates_count,
                         smoking_trend=None, smoking_label=None,
                         count_dfs=None, total_incidence_trend=None):
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
        "cancer_type_candidates_considered": cancer_type_candidates_count,
    }

    insights = _build_insights(county_values, trend_series, latest_year, debug)
    methodology = _build_methodology(latest_year, top5_meta, debug, smoking_label)

    state_average = (
        round(sum(v["value"] for v in county_values.values()) / len(county_values), 2)
        if county_values else None
    )

    measure_table = _build_measure_table(
        trend_series, count_dfs or {}, total_incidence_trend or {}, state_average, latest_year
    )

    return {
        "generated_at": int(time.time()),
        "latest_year": latest_year,
        "map_fig_json": map_fig_json,
        "county_values": county_values,  # {fips: {"name": ..., "value": ...}} — for the zip/county search
        "state_average": state_average,
        "trend_series": trend_series,
        "insights": insights,
        "methodology": methodology,
        "measure_table": measure_table,
        "debug": debug,
        "smoking_trend": smoking_trend or {},
        "smoking_label": smoking_label,
        "caveat": (
            "County-level (ecological) data, not individual-level evidence. "
            "Cell-size suppression removes any county-year built from too few "
            "underlying cases to protect privacy."
        ),
    }


def get_cancer_dashboard_data(force_refresh=False):
    """Public entry point used by app.py. Cached for _CACHE_TTL_SECONDS so
    the live CDC API isn't re-queried on every page view.

    On a cache miss, independent CDC API calls run CONCURRENTLY rather
    than one after another — the all-sites pull and the catalog fetch
    happen at the same time, and the final "pull full history for the
    top 5 winners + look up smoking data" step also runs in parallel.
    Combined with the concurrent ranking pass in
    _rank_top5_by_latest_year(), this cuts a cache-miss load from
    "dozens of sequential round-trips" down to roughly the time of the
    slowest handful running together.
    """
    now = time.time()
    if not force_refresh and _CACHE["data"] is not None and (now - _CACHE["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _CACHE["data"]

    token = _get_api_token()

    # All-sites pull and the measure catalog don't depend on each other —
    # fetch both at once instead of one after the other.
    with ThreadPoolExecutor(max_workers=2) as pool:
        all_sites_future = pool.submit(_with_retry, _pull_measure_df, token, ALL_SITES_MEASURE_ID, YEARS)
        catalog_future = pool.submit(_with_retry, _fetch_catalog, token)
        all_sites_df = all_sites_future.result()
        catalog = catalog_future.result()

    if all_sites_df.empty:
        raise RuntimeError("CDC API returned no usable (non-suppressed) all-sites rows for Texas.")
    latest_year = int(all_sites_df["temporalId"].dropna().max())

    candidates = _discover_cancer_type_measures(catalog)
    # _rank_top5_by_latest_year returns a (ranked_list, failure_count) tuple —
    # it must be unpacked here. Previously this was assigned directly to
    # top5_meta, which silently made top5_meta the whole 2-item tuple
    # (list, int). Iterating over that later (`for item in top5_meta`) then
    # yielded the ranked list itself as the first "item" and the int
    # failure count as the second, so `item["measure_id"]` blew up with
    # "list indices must be integers or slices, not str" as soon as any
    # code tried to treat an item as a per-measure dict.
    top5_meta, top5_ranking_failures = _rank_top5_by_latest_year(token, candidates, latest_year)
    smoking_measure = _discover_single_measure(
        catalog, keywords=["smoking", "smoker", "tobacco", "cigarette"]
    )
    annual_count_by_indicator = _discover_annual_count_measures(catalog)

    # Pull full multi-year history for the 5 winners, their companion
    # annual-case-count measures, the statewide total-cancers incidence
    # rate, AND the smoking measure (if found) — all at the same time,
    # since none of these pulls depend on each other.
    top5_dfs = {}
    count_dfs = {}
    total_incidence_trend = {}
    smoking_trend = {}
    smoking_label = None

    def _pull_full(measure_id):
        return _with_retry(_pull_state_measure_df, token, measure_id, YEARS)

    with ThreadPoolExecutor(max_workers=8) as pool:
        future_to_label = {
            pool.submit(_pull_full, item["measure_id"]): item["label"]
            for item in top5_meta
        }
        future_to_count_label = {
            pool.submit(_pull_full, annual_count_by_indicator[item["indicator_id"]]): item["label"]
            for item in top5_meta
            if item["indicator_id"] in annual_count_by_indicator
        }
        total_incidence_future = pool.submit(_pull_full, TOTAL_INCIDENCE_MEASURE_ID)
        smoking_future = pool.submit(_pull_full, smoking_measure["measure_id"]) if smoking_measure else None

        for future in as_completed(future_to_label):
            label = future_to_label[future]
            try:
                df = future.result()
            except Exception:
                continue
            if not df.empty:
                top5_dfs[label] = df

        for future in as_completed(future_to_count_label):
            label = future_to_count_label[future]
            try:
                df = future.result()
            except Exception:
                continue
            if not df.empty:
                count_dfs[label] = df

        try:
            total_df = total_incidence_future.result()
        except Exception:
            total_df = pd.DataFrame()
        if not total_df.empty:
            yearly = (total_df.groupby("temporalId")["dataValue"]
                      .mean().reset_index().sort_values("temporalId"))
            total_incidence_trend = {
                "years": yearly["temporalId"].astype(int).tolist(),
                "values": [round(v, 2) for v in yearly["dataValue"].tolist()],
            }

        if smoking_future is not None:
            try:
                smoking_df = smoking_future.result()
            except Exception:
                smoking_df = pd.DataFrame()
            if not smoking_df.empty:
                yearly = (smoking_df.groupby("temporalId")["dataValue"]
                          .mean().reset_index().sort_values("temporalId"))
                smoking_trend = {
                    "years": yearly["temporalId"].astype(int).tolist(),
                    "values": [round(v, 2) for v in yearly["dataValue"].tolist()],
                }
                smoking_label = smoking_measure["label"]

    shaped = _shape_for_frontend(all_sites_df, top5_dfs, top5_meta, len(candidates),
                                  smoking_trend, smoking_label,
                                  count_dfs, total_incidence_trend)
    shaped["debug"]["top5_ranking_failures"] = top5_ranking_failures
    _CACHE["data"] = shaped
    _CACHE["fetched_at"] = now
    return shaped