"""
CDC_Heart_Review.py

Live data layer for the Heart Disease & Stroke Data Analysis page
(/blog/heart_report). Pulls two things from the CDC Environmental Public
Health Tracking Network (EPHTN) API, same pattern as CDC_Review.py
(cancer):

  1. County-level crude prevalence of Coronary Heart Disease among
     adults for the most recent year -> rendered as a Plotly choropleth
     map (measure 1092).
  2. Statewide yearly age-adjusted MORTALITY trends for the three heart
     disease/stroke causes EPHTN's "Heart Disease & Stroke" content area
     actually publishes yearly death-rate measures for: Heart Attack,
     Ischemic Heart Disease, and Stroke -> a 3-line Chart.js chart.

Unlike CDC_Review.py's cancer-type discovery/ranking (needed because
EPHTN tracks dozens of site-specific cancers), these three causes are a
fixed, curated list - EPHTN's Heart Disease & Stroke content area simply
doesn't have dozens of interchangeable "types" to rank the way cancer
does, so a hardcoded set here is a more honest fit than forcing the same
discovery machinery onto a differently-shaped dataset. See
_build_methodology() for how this is disclosed in the page itself.
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from cdc_ephtn_client import (
    get_api_token as _get_api_token,
    with_retry as _with_retry,
    pull_measure_df as _pull_measure_df,
    pull_state_measure_df as _pull_state_measure_df,
    to_fips as _to_fips,
    build_choropleth as _build_choropleth_generic,
)

COUNTY_PREVALENCE_MEASURE_ID = 1092  # Crude Prevalence of Coronary Heart Disease among Adults (County)
YEARS = [str(y) for y in range(2014, 2023)]

# label -> (age-adjusted mortality rate measureId, annual death count measureId or None)
MORTALITY_CAUSES = {
    "Heart Attack": (1405, 1403),
    "Ischemic Heart Disease": (1402, 1400),
    "Stroke": (1151, None),  # EPHTN publishes no separate annual-count variant for this measure
}

# label -> state-level age-adjusted prevalence measureId, shown for context
RISK_FACTORS = {
    "Current Smoking": 1361,
    "High Blood Pressure": 1357,
    "High Cholesterol": 1359,
}

# In-memory cache so the CDC API isn't re-queried on every page view.
_CACHE = {"data": None, "fetched_at": 0}
_CACHE_TTL_SECONDS = 60 * 60  # 1 hour


def _build_choropleth(county_values, latest_year):
    return _build_choropleth_generic(
        county_values,
        title=f"Coronary Heart Disease Prevalence by County — {latest_year}",
        colorbar_title="Crude Prevalence (%)",
        value_suffix="%",
        hover_label="Prevalence",
    )


def _build_insights(county_values, trend_series, latest_year, debug):
    """Five data-driven takeaways, computed from whatever actually came
    back this pull - not static/hardcoded text. Mirrors CDC_Review.py's
    _build_insights, adapted to mortality causes instead of cancer types."""
    insights = []

    if county_values:
        by_value = sorted(county_values.items(), key=lambda kv: kv[1]["value"])
        lowest = by_value[0][1]
        highest = by_value[-1][1]
        insights.append(
            f"{highest['name']} had the highest reported crude coronary heart disease "
            f"prevalence in {latest_year} at {highest['value']}%, compared to "
            f"{lowest['name']} at {lowest['value']}%."
        )

    if trend_series:
        deltas = {}
        for label, series in trend_series.items():
            vals = [v for v in series["values"] if v is not None]
            if len(vals) >= 2:
                deltas[label] = vals[-1] - vals[0]
        if deltas:
            # Rank by magnitude of change (not signed value) so "biggest
            # mover" means the largest actual change in either direction -
            # mortality trends mostly decline, but this shouldn't assume that.
            biggest_mover = max(deltas, key=lambda k: abs(deltas[k]))
            steadiest = min(deltas, key=lambda k: abs(deltas[k]))
            mover_delta = deltas[biggest_mover]
            verb = "fell" if mover_delta < 0 else "rose"
            insights.append(
                f"Among the three tracked causes, {biggest_mover} mortality {verb} the most "
                f"over its tracked period ({mover_delta:+.2f} deaths per 100,000 people, "
                "age-adjusted)."
            )
            if steadiest != biggest_mover:
                insights.append(
                    f"{steadiest} mortality changed the least over its own tracked period "
                    f"({deltas[steadiest]:+.2f} deaths per 100,000 people)."
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
                f"Trends span {years[0]}–{years[-1]}, though each cause has its own reporting "
                f"window — the three lines don't all cover identical years."
            )

    fallback_insights = [
        f"The dashboard includes reportable data for {len(county_values)} of 254 Texas counties.",
        f"The latest county data year is {latest_year}.",
        "County values are crude rates and are not adjusted for differences in age.",
        "CDC suppresses some values when case counts are too small to protect privacy.",
        "Mortality trends are calculated separately from the county prevalence map.",
    ]

    for insight in fallback_insights:
        if len(insights) >= 5:
            break
        if insight not in insights:
            insights.append(insight)

    return insights[:5]


def _build_methodology(latest_year, debug, risk_factor_labels):
    cause_list = ", ".join(MORTALITY_CAUSES.keys())
    risk_list = ", ".join(risk_factor_labels) if risk_factor_labels else "none available this pull"

    return {
        "source": (
            "All figures on this page come from the CDC's Environmental Public Health "
            "Tracking Network (EPHTN) API, pulled live on each page load (cached for an "
            f"hour). The map uses measure {COUNTY_PREVALENCE_MEASURE_ID} ('Crude Prevalence "
            "of Coronary Heart Disease among Adults'); the trend lines use EPHTN's published "
            "age-adjusted death-rate measures for each cause."
        ),
        "measure_definition": (
            "The map shows PREVALENCE, not mortality: a model-based estimate of the "
            "percentage of adults in each county who have EVER been diagnosed with coronary "
            "heart disease (a lifetime history, self-reported in the underlying survey data "
            "and modeled down to the county level) — not deaths in a given year. 'Crude' "
            "means it is not adjusted for age, so a county with an older population will "
            "tend to show a higher value than a younger one, independent of any environmental "
            "or behavioral factor. The three-line trend chart is a different kind of "
            "statistic: age-adjusted MORTALITY rates — deaths per 100,000 people per year, "
            "drawn from death-certificate data — for Heart Attack, Ischemic Heart Disease, "
            "and Stroke specifically, not coronary heart disease prevalence as a whole. "
            "Prevalence and mortality numbers on this page are not directly comparable to "
            "each other."
        ),
        "cause_selection": (
            f"The three lines shown ({cause_list}) are a fixed, curated set, not an "
            "algorithmic discovery/ranking like the cancer-type dashboard uses — EPHTN's "
            "Heart Disease & Stroke content area publishes yearly age-adjusted mortality "
            "rates for exactly these three causes (plus a handful of hospitalization and "
            "risk-factor measures that aren't directly comparable death rates), so there "
            "isn't an open-ended list to rank here the way there is for cancer types."
        ),
        "limitations": [
            "Ecological, not individual-level: these are county- or state-level averages, "
            "not records tied to individual patients — they can suggest patterns but not "
            "prove causes.",
            "Cell-size suppression: CDC withholds any county-year built from too few "
            "underlying cases to protect patient privacy, so low-population counties "
            "are more likely to have gaps than high-population ones, in any given year.",
            "Different reporting windows: each mortality cause has its own available year "
            "range in EPHTN's catalog, so the three trend lines don't all span identical "
            "years — read each line's own endpoints rather than assuming a shared timeline.",
            "Reporting lag: death-certificate data takes time to finalize, so the most "
            "recent year or two shown may still be revised as more records are processed.",
            (
                f"Risk-factor context ({risk_list}): EPHTN doesn't link individual risk-factor "
                "status to individual mortality records, so any relationship between the "
                "risk-factor chart and the mortality trends on this page is suggestive at "
                "the population level only, not a tested link."
            ),
        ],
    }


def _build_measure_table(trend_series, count_dfs):
    """Age-adjusted mortality rate + annual death count (where EPHTN
    publishes one) for each cause, each using its own latest available
    year — mirrors CDC_Review.py's _build_measure_table."""
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
            "annual_deaths": count_value,
        })
    return {"rows": rows}


def _shape_for_frontend(county_df, trend_dfs, count_dfs, risk_factor_dfs):
    latest_year = int(county_df["temporalId"].dropna().max())
    latest = county_df[county_df["temporalId"] == latest_year].dropna(subset=["dataValue"])

    county_values = {
        _to_fips(row.geoId): {"name": row.geo, "value": round(float(row.dataValue), 2)}
        for row in latest.itertuples()
    }

    map_fig_json = _build_choropleth(county_values, latest_year)

    trend_series = {}
    for label, df in trend_dfs.items():
        if df.empty:
            continue
        yearly = df.groupby("temporalId")["dataValue"].mean().reset_index().sort_values("temporalId")
        trend_series[label] = {
            "years": yearly["temporalId"].astype(int).tolist(),
            "values": [round(v, 2) for v in yearly["dataValue"].tolist()],
        }

    risk_factor_trend = {}
    for label, df in risk_factor_dfs.items():
        if df.empty:
            continue
        yearly = df.groupby("temporalId")["dataValue"].mean().reset_index().sort_values("temporalId")
        risk_factor_trend[label] = {
            "years": yearly["temporalId"].astype(int).tolist(),
            "values": [round(v, 2) for v in yearly["dataValue"].tolist()],
        }

    debug = {
        "total_rows_county": int(len(county_df)),
        "rows_for_latest_year": int(len(latest)),
        "counties_matched": len(county_values),
        "causes_found": list(trend_series.keys()),
        "risk_factors_found": list(risk_factor_trend.keys()),
    }

    insights = _build_insights(county_values, trend_series, latest_year, debug)
    methodology = _build_methodology(latest_year, debug, list(risk_factor_trend.keys()))

    state_average = (
        round(sum(v["value"] for v in county_values.values()) / len(county_values), 2)
        if county_values else None
    )

    measure_table = _build_measure_table(trend_series, count_dfs)

    return {
        "generated_at": int(time.time()),
        "latest_year": latest_year,
        "map_fig_json": map_fig_json,
        "county_values": county_values,
        "state_average": state_average,
        "trend_series": trend_series,
        "insights": insights,
        "methodology": methodology,
        "measure_table": measure_table,
        "debug": debug,
        "risk_factor_trend": risk_factor_trend,
        "caveat": (
            "County-level (ecological) data, not individual-level evidence. "
            "Cell-size suppression removes any county-year built from too few "
            "underlying cases to protect privacy."
        ),
    }


def get_heart_dashboard_data(force_refresh=False):
    """Public entry point used by app.py. Cached for _CACHE_TTL_SECONDS so
    the live CDC API isn't re-queried on every page view.

    All pulls (county map, 3 mortality causes + their count companions,
    3 risk-factor context measures) are independent of each other, so
    they all run concurrently rather than sequentially."""
    now = time.time()
    if not force_refresh and _CACHE["data"] is not None and (now - _CACHE["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _CACHE["data"]

    token = _get_api_token()

    def _pull_county():
        return _with_retry(_pull_measure_df, token, COUNTY_PREVALENCE_MEASURE_ID, YEARS)

    def _pull_state(measure_id):
        return _with_retry(_pull_state_measure_df, token, measure_id, YEARS)

    trend_dfs = {}
    count_dfs = {}
    risk_factor_dfs = {}

    with ThreadPoolExecutor(max_workers=8) as pool:
        county_future = pool.submit(_pull_county)

        future_to_label = {
            pool.submit(_pull_state, rate_id): label
            for label, (rate_id, _count_id) in MORTALITY_CAUSES.items()
        }
        future_to_count_label = {
            pool.submit(_pull_state, count_id): label
            for label, (_rate_id, count_id) in MORTALITY_CAUSES.items()
            if count_id is not None
        }
        future_to_risk_label = {
            pool.submit(_pull_state, measure_id): label
            for label, measure_id in RISK_FACTORS.items()
        }

        county_df = county_future.result()

        for future in as_completed(future_to_label):
            label = future_to_label[future]
            try:
                df = future.result()
            except Exception:
                continue
            if not df.empty:
                trend_dfs[label] = df

        for future in as_completed(future_to_count_label):
            label = future_to_count_label[future]
            try:
                df = future.result()
            except Exception:
                continue
            if not df.empty:
                count_dfs[label] = df

        for future in as_completed(future_to_risk_label):
            label = future_to_risk_label[future]
            try:
                df = future.result()
            except Exception:
                continue
            if not df.empty:
                risk_factor_dfs[label] = df

    if county_df.empty:
        raise RuntimeError("CDC API returned no usable (non-suppressed) county rows for Texas.")

    shaped = _shape_for_frontend(county_df, trend_dfs, count_dfs, risk_factor_dfs)
    _CACHE["data"] = shaped
    _CACHE["fetched_at"] = now
    return shaped
