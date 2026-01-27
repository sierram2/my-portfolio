def get_active_users_json(client, property_id):
    import pandas as pd
    import datetime as dt
    from google.analytics.data_v1beta.types import (
        RunReportRequest, Dimension, Metric
    )

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="activeUsers")],
        date_ranges=[{"start_date": "30daysAgo", "end_date": "today"}],
    )

    response = client.run_report(request)

    dates = []
    users = []

    for row in response.rows:
        dates.append(dt.datetime.strptime(row.dimension_values[0].value, "%Y%m%d"))
        users.append(int(row.metric_values[0].value))

    df = pd.DataFrame({
        "date": dates,
        "active_users": users
    })

    return df
