# -*- coding: utf-8 -*-
"""
Created on Fri Sep 12 16:48:35 2025
@author: sierram2
"""
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, Dimension, Metric
from google.oauth2 import service_account
import pandas as pd
import datetime as dt
import io
import base64
import matplotlib.pyplot as plt

# --- Authentication ---
KEY_PATH = r"\\ifs\links\P5510-OSPBD\Data and Analytics\Users\SierraM2\Python\google_key.json"
credentials = service_account.Credentials.from_service_account_file(KEY_PATH)
client = BetaAnalyticsDataClient(credentials=credentials)

PROPERTY_ID = "504615296"


# ✅ Wrap everything in a function Flask can call
def get_data():
    # --- GA4 Query ---
    request_daily = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="activeUsers")],
        date_ranges=[{"start_date": "7daysAgo", "end_date": "today"}],
    )

    response_daily = client.run_report(request_daily)

    # --- Process data ---
    dates_raw = [row.dimension_values[0].value for row in response_daily.rows]
    active_users_daily = [int(row.metric_values[0].value) for row in response_daily.rows]

    dates_parsed = [dt.datetime.strptime(d, "%Y%m%d") for d in dates_raw]

    df_daily = pd.DataFrame({
        "date": dates_parsed,
        "active_users": active_users_daily
    })

    df_daily = df_daily.set_index("date").resample("D").sum().reset_index()

    # --- Create plot in memory (no GUI popup) ---
    plt.figure(figsize=(8, 4))
    plt.bar(df_daily["date"].dt.strftime("%b %d"), df_daily["active_users"], color="skyblue")
    plt.title("Active Users (Last 7 Days)")
    plt.xlabel("Date")
    plt.ylabel("Active Users")
    plt.xticks(rotation=45)
    plt.tight_layout()

    # Save chart to memory as base64
    buffer = io.BytesIO()
    plt.savefig(buffer, format='png')
    buffer.seek(0)
    image_base64 = base64.b64encode(buffer.read()).decode('utf-8')
    plt.close()

    # Return the chart HTML
    return f'<img src="data:image/png;base64,{image_base64}" alt="Active Users Chart">'
