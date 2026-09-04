from flask import Flask, jsonify, render_template
import json
import os

from CDC_Review import get_cancer_dashboard_data

app = Flask(__name__)


def load_projects():
    json_path = os.path.join(app.root_path, 'projects.json')
    try:
        with open(json_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []


# Home page route
@app.route("/")
def home():
    return render_template("index.html", projects=load_projects())


# Projects page route
@app.route("/projects")
def projects():
    return render_template("projects.html", projects=load_projects())


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


# Cancer data dashboard — pulls live from CDC_Review.py (CDC EPHTN API),
# cached for an hour inside get_cancer_dashboard_data(). On any failure
# (missing token, CDC API down, etc.) we render the same template with
# error set instead of data, which is what cancer_report.html's
# {% if error %} block expects.
@app.route("/blog/cancer_report")
def cancer_analysis():
    try:
        data = get_cancer_dashboard_data()
        return render_template("cancer_report.html", data=data, error=None)
    except Exception as e:
        return render_template("cancer_report.html", data=None, error=str(e))


@app.route("/blog/<post_id>")
def blog_post(post_id):
    return render_template(f"blog/{post_id}.html")


# Dynamic page routing - This MUST be last since it's a catch-all
@app.route("/<page>")
def render_page(page):
    try:
        return render_template(f"{page}.html")
    except Exception:
        return render_template("404.html"), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)