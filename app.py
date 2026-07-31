from flask import Flask, jsonify, render_template, request
from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from analytics.ga_daily import get_active_users_json, get_traffic_sources
from CDC_Review import get_cancer_dashboard_data
import json
import os 

app = Flask(__name__)

# --- GOOGLE ANALYTICS CREDENTIALS LOGIC ---
# This checks if we are on Render (Env Var) or Local (File)
if os.environ.get("GOOGLE_CREDENTIALS_JSON"):
    # This runs on Render using the environment variable
    info = json.loads(os.environ.get("GOOGLE_CREDENTIALS_JSON"))
    credentials = service_account.Credentials.from_service_account_info(info)
else:
    # This runs on your computer using your local file
    # Make sure this file is in your gitignore so it doesn't go to GitHub!
    credentials = service_account.Credentials.from_service_account_file("google_key.json")

client = BetaAnalyticsDataClient(credentials=credentials)
PROPERTY_ID = "504615296"
# ------------------------------------------

# API route for active users
@app.route("/api/active-users")
def active_users():
    df = get_active_users_json(client, PROPERTY_ID)
    return df.to_json(orient="records", date_format="iso")

# API route for the cancer dashboard data (used by cancer_report.html's Chart.js,
# and reusable if you want to pull the same data into another page later)
@app.route("/api/cancer-data")
def cancer_data():
    force_refresh = request.args.get("refresh") == "1"
    try:
        data = get_cancer_dashboard_data(force_refresh=force_refresh)
    except RuntimeError as e:
        # Missing API token, etc. — surface a clean error instead of a 500 traceback
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": "Failed to fetch CDC data", "detail": str(e)}), 502
    return jsonify(data)

# Projects page route
@app.route("/projects")
def projects():
    json_path = os.path.join(app.root_path, 'projects.json')
    try:
        with open(json_path, 'r') as f:
            project_data = json.load(f)
    except FileNotFoundError:
        project_data = []
    return render_template("projects.html", projects=project_data)

# BLOG ROUTES - Must come BEFORE the catch-all route
@app.route("/blog")
def blog_index():
    json_path = os.path.join(app.root_path, 'blog_posts.json')
    try:
        with open(json_path, 'r') as f:
            posts = json.load(f)
        posts.sort(key=lambda x: x['date'], reverse=True)
    except FileNotFoundError:
        posts = []
    return render_template("blog.html", posts=posts)

@app.route("/blog/ga4-report")
def ga4_report():
    df = get_active_users_json(client, PROPERTY_ID)
    report_data = df.to_dict(orient="records")
    
    # Get traffic sources from GA4
    traffic_sources = get_traffic_sources(client, PROPERTY_ID)
    
    return render_template("ga4_report.html", 
                         report=report_data,
                         sources=traffic_sources)

@app.route("/blog/cancer-analysis")
def cancer_analysis():
    try:
        data = get_cancer_dashboard_data()
        error = None
    except RuntimeError as exc:
        data = None
        error = str(exc)
    except Exception:
        app.logger.exception("Unable to load cancer dashboard")
        data = None
        error = "The CDC data is temporarily unavailable."

    return render_template(
        "cancer_report.html",
        data=data,
        error=error
    )

@app.route("/blog/cancer_report")
def cancer_report():
    return render_template("cancer_report.html")

@app.route("/blog/<post_id>")
def blog_post(post_id):
    return render_template(f"blog/{post_id}.html")

# Dynamic page routing - This MUST be last since it's a catch-all
@app.route("/", defaults={"page": "index"})
@app.route("/<page>")
def render_page(page):
    try:
        return render_template(f"{page}.html")
    except:
        return render_template("404.html"), 404

@app.route("/api/cancer-dashboard")
def cancer_dashboard_api():
    try:
        data = get_cancer_dashboard_data()
        return jsonify(data)
    except Exception as error:
        app.logger.exception("Cancer dashboard data request failed")
        return jsonify({"error": str(error)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)