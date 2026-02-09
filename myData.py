# -*- coding: utf-8 -*-
"""
Created on Fri Sep 12 16:48:35 2025
@author: sierram2
"""
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, Dimension, Metric, DateRange
from google.oauth2 import service_account
import pandas as pd
import datetime as dt

# This function returns active users data as JSON for Chart.js
def get_active_users_json(client, property_id):
    """
    Fetches daily active users from GA4 for the last 60 days.
    Returns a pandas DataFrame with 'date' and 'active_users' columns.
    """
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="activeUsers")],
        date_ranges=[DateRange(start_date="60daysAgo", end_date="today")],
    )

    response = client.run_report(request)

    # Process data
    dates_raw = [row.dimension_values[0].value for row in response.rows]
    active_users = [int(row.metric_values[0].value) for row in response.rows]

    # Parse dates
    dates_parsed = [dt.datetime.strptime(d, "%Y%m%d") for d in dates_raw]

    # Create DataFrame
    df = pd.DataFrame({
        "date": dates_parsed,
        "active_users": active_users
    })

    return df


def get_traffic_sources(client, property_id):
    """
    Fetches traffic source breakdown from GA4 for the last 30 days.
    Returns a dictionary with channel names as keys and percentages as values.
    """
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="sessions")],
        date_ranges=[DateRange(start_date="30daysAgo", end_date="yesterday")],
    )
    
    response = client.run_report(request)
    
    # Process the response
    sources = {}
    total_sessions = 0
    
    for row in response.rows:
        channel = row.dimension_values[0].value
        sessions = int(row.metric_values[0].value)
        sources[channel] = sessions
        total_sessions += sessions
    
    # Convert to percentages and round to 1 decimal
    if total_sessions > 0:
        source_percentages = {
            channel: round((sessions / total_sessions) * 100, 1)
            for channel, sessions in sources.items()
        }
    else:
        source_percentages = {}
    
    return source_percentages