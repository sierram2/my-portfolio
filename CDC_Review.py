import pandas as pd
import geopandas
import matplotlib.pyplot as plt
import json
import io
import os

# --- Configuration ---
CSV_FILE_NAME = "breast_cancer_data_texas_counties.csv"
# The path you provided for your file:
GEOJSON_PATH = r"C:\Users\sierram2\Downloads\tx_counties.geojson" 

# ====================================================================
# PART 1: LOAD BREAST CANCER DATA (from the CSV you previously created)
# ====================================================================
try:
    df_cancer = pd.read_csv(CSV_FILE_NAME)
    print(f"✅ Loaded data from existing file: {CSV_FILE_NAME}.")
except Exception as e:
    print(f"❌ FATAL ERROR: Could not load existing CSV file. Error: {e}")
    print("Please ensure the CSV file exists in your current working directory.")
    exit()

# ====================================================================
# PART 2: LOAD GEOJSON (Using Workaround for Fiona Error)
# ====================================================================
try:
    # Workaround: Read GeoJSON as raw text
    with open(GEOJSON_PATH, 'r', encoding='utf-8') as f:
        geojson_content = f.read()
    
    # Load content into GeoDataFrame
    gdf = geopandas.read_file(io.StringIO(geojson_content))
    print(f"✅ GeoJSON boundaries loaded successfully using content workaround.")

except Exception as e:
    print(f"❌ FATAL ERROR loading GeoJSON file. Check path/file integrity. Error: {e}")
    print("Cannot create map without geographical data.")
    exit()

# ====================================================================
# PART 3: PREPARE DATA AND MERGE (FINAL FIPS FIXES APPLIED)
# ====================================================================

# 1. Prepare GeoJSON FIPS 
if 'FIPS' in gdf.columns:
    # Ensure FIPS is string and 5 digits (e.g., "48111")
    gdf['FIPS_KEY'] = gdf['FIPS'].astype(str).str.zfill(5)
else:
    print("❌ GeoJSON does not contain the 'FIPS' column. Cannot merge.")
    exit()

# 2. Prepare Cancer Data FIPS and isolate latest year
FIPS_DATA_COL = 'geoId' 
if FIPS_DATA_COL not in df_cancer.columns:
    print(f"❌ CSV Data missing expected FIPS column: '{FIPS_DATA_COL}'.")
    exit()

df_cancer['FIPS_KEY'] = df_cancer[FIPS_DATA_COL].astype(str).str.zfill(5)

# Isolate the latest year's data
latest_year = df_cancer['temporal'].max()
df_data_map = (
    df_cancer[df_cancer['temporal'] == latest_year][['FIPS_KEY', 'dataValue']]
    .rename(columns={'dataValue': 'Cancer_Rate'})
)

# 3. Merge on the common key: 'FIPS_KEY'
gdf_merged = gdf.merge(df_data_map, on='FIPS_KEY', how='left')
gdf_merged_clean = gdf_merged.dropna(subset=['Cancer_Rate'])

print(f"✅ Data successfully merged. {gdf_merged_clean.shape[0]} counties available for mapping.")

