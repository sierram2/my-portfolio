from flask import Flask, jsonify, render_template
from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from analytics.ga_daily import get_active_users_json
import json  # Import json to read your project file
import os    # Good for checking if the file exists

app = Flask(__name__)

# Google Analytics credentials
credentials = service_account.Credentials.from_service_account_file(
    "google_key.json"
)
client = BetaAnalyticsDataClient(credentials=credentials)
PROPERTY_ID = "504615296"

# API route for active users
@app.route("/api/active-users")
def active_users():
    df = get_active_users_json(client, PROPERTY_ID)
    return df.to_json(orient="records", date_format="iso")

# Projects page route (FIXED for Step 2)
@app.route("/projects")
def projects():
    # 1. Look for the projects.json file in your root folder
    json_path = os.path.join(app.root_path, 'projects.json')
    
    try:
        with open(json_path, 'r') as f:
            project_data = json.load(f)
    except FileNotFoundError:
        # Fallback if you haven't created the file yet
        project_data = []

    # 2. Pass the 'project_data' list to the template as a variable named 'projects'
    return render_template("projects.html", projects=project_data)

# Dynamic page routing for all other HTML pages
@app.route("/", defaults={"page": "index"})
@app.route("/<page>")
def render_page(page):
    try:
        return render_template(f"{page}.html")
    except:
        return render_template("404.html"), 404

@app.route("/blog/ga4-report")
def ga4_report():
    # Fetch the live data
    df = get_active_users_json(client, PROPERTY_ID)
    
    # --- DEBUG LINE ---
    print(f"DEBUG: GA4 returned {len(df)} rows of data.")
    # ------------------

    # Convert to list of dicts for the template
    report_data = df.to_dict(orient="records")
    return render_template("ga4_report.html", report=report_data)

from waitress import serve

if __name__ == "__main__":
    # serve(app, host='0.0.0.0', port=5000)
    app.run(debug=True) # Keep this for dev, use serve for prod