import os
import requests
import pandas as pd
import matplotlib.pyplot as plt

# ── Config ────────────────────────────────────────────────────
API_TOKEN  = "CDC_API_TOKEN.txt"
BASE       = "https://ephtracking.cdc.gov/apigateway/api/v1"
OUTPUT_DIR = "ephtn_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Pull Data ─────────────────────────────────────────────────
url = (f"{BASE}/getCoreHolder/1095/2/1/48/1/2021,2020,2019,2018,2017,2016,2015/0/0")
r   = requests.get(url, params={"apiToken": API_TOKEN}, timeout=90)
df  = pd.DataFrame(r.json()["tableResult"])
df.columns = [c.lower() for c in df.columns]

# ── Aggregate by year ─────────────────────────────────────────
df["datavalue"] = pd.to_numeric(df["datavalue"], errors="coerce")
df["temporalid"] = pd.to_numeric(df["temporalid"], errors="coerce")
yearly = df.groupby("temporalid")["datavalue"].mean().reset_index()
yearly.columns = ["year", "avg_prevalence"]
yearly = yearly.sort_values("year")

# ── Bar Chart ─────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(10, 6))
bars = ax.bar(yearly["year"].astype(int), yearly["avg_prevalence"],
              color="#e34a33", edgecolor="#111", linewidth=0.5)

for bar, val in zip(bars, yearly["avg_prevalence"]):
    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
            f"{val:.2f}%", ha="center", va="bottom", fontsize=9)

ax.set_xlabel("Year", fontsize=12)
ax.set_ylabel("Avg Crude Prevalence (%)", fontsize=12)
ax.set_title("Crude Cancer Prevalence by Year · Texas", fontsize=14, fontweight="bold")
ax.set_xticks(yearly["year"].astype(int))
ax.yaxis.grid(True, linestyle="--", alpha=0.4)
ax.set_axisbelow(True)

plt.tight_layout()
path = os.path.join(OUTPUT_DIR, "tx_cancer_by_year.png")
plt.savefig(path, dpi=150, bbox_inches="tight")
print(f"Saved → {path}")
plt.show()


