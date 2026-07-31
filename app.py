import json
import os

from flask import Flask, jsonify, render_template, request
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.oauth2 import service_account

from analytics.ga_daily import get_active_users_json, get_traffic_sources
from CDC_Review import get_cancer_dashboard_data


app = Flask(__name__)


# --- GOOGLE ANALYTICS CREDENTIALS ---

credentials_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")

if credentials_json:
    # Render deployment
    credentials_info = json.loads(credentials_json)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info
    )
else:
    # Local development
    credentials = service_account.Credentials.from_service_account_file(
        os.path.join(app.root_path, "google_key.json")
    )

client = BetaAnalyticsDataClient(credentials=credentials)
PROPERTY_ID = "504615296"


# --- GOOGLE ANALYTICS API ---

@app.route("/api/active-users")
def active_users():
    df = get_active_users_json(client, PROPERTY_ID)

    return df.to_json(
        orient="records",
        date_format="iso"
    )


# --- CDC CANCER DATA API ---

@app.route("/api/cancer-data")
@app.route("/api/cancer-dashboard")
def cancer_data():
    force_refresh = request.args.get("refresh") == "1"

    try:
        data = get_cancer_dashboard_data(
            force_refresh=force_refresh
        )

        return jsonify(data)

    except RuntimeError as error:
        app.logger.exception("CDC configuration error")
        return jsonify({
            "error": str(error)
        }), 503

    except Exception as error:
        app.logger.exception("CDC data request failed")
        return jsonify({
            "error": "Failed to fetch CDC data",
            "detail": str(error)
        }), 502


# --- PROJECTS PAGE ---

@app.route("/projects")
def projects():
    json_path = os.path.join(
        app.root_path,
        "projects.json"
    )

    try:
        with open(json_path, "r", encoding="utf-8") as file:
            project_data = json.load(file)

    except FileNotFoundError:
        project_data = []

    return render_template(
        "projects.html",
        projects=project_data
    )


# --- BLOG INDEX ---

@app.route("/blog")
def blog_index():
    json_path = os.path.join(
        app.root_path,
        "blog_posts.json"
    )

    try:
        with open(json_path, "r", encoding="utf-8") as file:
            posts = json.load(file)

        posts.sort(
            key=lambda post: post.get("date", ""),
            reverse=True
        )

    except FileNotFoundError:
        posts = []

    return render_template(
        "blog.html",
        posts=posts
    )


# --- GOOGLE ANALYTICS REPORT ---

@app.route("/blog/ga4-report")
def ga4_report():
    df = get_active_users_json(
        client,
        PROPERTY_ID
    )

    report_data = df.to_dict(
        orient="records"
    )

    traffic_sources = get_traffic_sources(
        client,
        PROPERTY_ID
    )

    return render_template(
        "ga4_report.html",
        report=report_data,
        sources=traffic_sources
    )


# --- CDC CANCER REPORT PAGE ---

@app.route("/blog/cancer_report")
@app.route("/blog/cancer-analysis")
def cancer_report():
    try:
        data = get_cancer_dashboard_data()
        error = None

    except RuntimeError as error:
        app.logger.exception("CDC configuration error")
        data = None
        error = str(error)

    except Exception:
        app.logger.exception("Unable to load the cancer dashboard")
        data = None
        error = "The CDC data is temporarily unavailable."

    return render_template(
        "cancer_report.html",
        data=data,
        error=error
    )


# --- INDIVIDUAL BLOG POSTS ---

@app.route("/blog/<post_id>")
def blog_post(post_id):
    return render_template(
        f"blog/{post_id}.html"
    )


# --- STATIC PAGE ROUTING ---
# Keep this route last because it is a catch-all route.

@app.route("/")
@app.route("/<page>")
def render_page(page="index"):
    try:
        return render_template(
            f"{page}.html"
        )

    except Exception:
        return render_template(
            "404.html"
        ), 404


# --- APPLICATION STARTUP ---

if __name__ == "__main__":
    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )