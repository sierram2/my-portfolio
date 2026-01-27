from flask import Flask, jsonify, render_template
from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from analytics.ga_daily import get_active_users_json

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

# Projects page route
@app.route("/projects")
def projects():
    return render_template("projects.html")

# Dynamic page routing for all other HTML pages
@app.route("/", defaults={"page": "index"})
@app.route("/<page>")
def render_page(page):
    try:
        return render_template(f"{page}.html")
    except:
        # Optional: log the error here if needed
        return render_template("404.html"), 404

if __name__ == "__main__":
    app.run(debug=True)
