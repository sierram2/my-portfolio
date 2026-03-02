# -*- coding: utf-8 -*-
"""
Created on Mon Mar 17 16:06:33 2025
@author: burnettc

*** Update to use 30 as threshold instead! ***
"""

# %% Library Imports
import pandas as pd
import geopandas as gpd
from geopy.distance import geodesic
import time
import folium
import os
from math import ceil
import numpy as np
import sqlalchemy
import pyodbc
import matplotlib.pyplot as plt
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

# %% SQL Connection
start_time = time.time()

SERVER = 'prd-clarity.et0582.epichosted.com'
DATABASE = 'clarity'

connectionString = (
    f'DRIVER={{ODBC Driver 17 for SQL Server}};'
    f'SERVER={SERVER};'
    f'DATABASE={DATABASE};'
    f'Encrypt=yes;'
    f'Trusted_Connection=yes;'
    f'TrustServerCertificate=yes;'
)

connection_url = URL.create("mssql+pyodbc", query={"odbc_connect": connectionString})
engine = create_engine(connection_url)

SQL_QUERY = """
    SELECT DISTINCT
        Count(PAT_ID) AS PAT_Count,
        [ZIP]
    FROM [CLARITY].[UthX].[TransactionDatamart]
    WHERE [svc_date] BETWEEN '2024-09-01' AND '2025-08-31' /**Change for required FY**/
    GROUP BY ZIP
"""

df = pd.read_sql(SQL_QUERY, engine)
print(f"Rows returned: {len(df)}")
print(df.describe())
print(df.head())
print("SQL Import Complete")

# %% Clean ZIP Codes
df.dropna(inplace=True)
df['ZIP'] = df['ZIP'].str.split('-').str[0].str[:5]
df = df[df['ZIP'].str.match(r'^\d{5}$')]   # Exactly 5 digits
df['ZIP'] = df['ZIP'].str.zfill(5)          # Preserve leading zeros
df = df.rename(columns={'ZIP': 'zipCode'})

data = df.copy()
print(f"Clean data shape: {data.shape}")

# %% Rank & Percentile by Patient Count
data['rank'] = data['PAT_Count'].rank(method='min', ascending=False).astype(int)
total = len(data)

data['percent'] = data['rank'].apply(lambda x: 100 - ((x - 1) / total * 100))
data['percent_label'] = data['percent'].apply(lambda x: f"{x:.2f}%")
print("Ranking and percentiles calculated")

# %% Load ZIP Code Coordinates (Local Lookup - Replaces Geocoding)
print("Loading ZIP coordinate reference file...")
lookup_start = time.time()

zip_ref = pd.read_csv("uszips.csv", dtype={'zip': str})
zip_ref = zip_ref[['zip', 'lat', 'lng']]
zip_ref.columns = ['zipCode', 'latitude', 'longitude']

# Merge coordinates into data
data = data.merge(zip_ref, on='zipCode', how='left')
data = data[data['latitude'].notnull()]             # Drop ZIPs not found in reference
data["coordinates"] = list(zip(data["latitude"], data["longitude"]))

print(f"Coordinate lookup completed in {time.time() - lookup_start:.2f} seconds.")
print(f"Rows after coordinate merge: {len(data)}")

# %% Reference ZIP Setup
reference_zip = "78229"
ref_row = zip_ref[zip_ref['zipCode'] == reference_zip].iloc[0]
ref_coords = (ref_row['latitude'], ref_row['longitude'])
print(f"Reference ZIP {reference_zip} coords: {ref_coords}")

# %% Distance Calculations
print("Calculating distances...")
dist_start = time.time()

data["distanceKM"] = data["coordinates"].apply(lambda x: geodesic(ref_coords, x).km)
data["distanceMI"] = data["distanceKM"] * 0.621371

print(f"Distance calculations completed in {time.time() - dist_start:.2f} seconds.")

# %% Assign PSA / SSA / Outside
def assign_psassa(row):
    dist_mi = ceil(row['distanceMI'])
    percent = ceil(row['percent'])
    if dist_mi <= 50 and percent >= 80:
        return "Primary"
    elif (51 <= dist_mi <= 140 and 40 <= percent < 80) or (dist_mi > 50 and percent >= 80):
        return "Secondary"
    else:
        return "Outside"

data = data[data['PAT_Count'] > 30]
data = data.drop_duplicates(subset='zipCode')
data["PSA_SSA"] = data.apply(assign_psassa, axis=1)
data = data.dropna(subset=["coordinates"])

counts = data['PSA_SSA'].value_counts(dropna=False)
print("PSA/SSA Assignment Counts:")
print(counts)

# %% Export
print(f"Current directory: {os.getcwd()}")
data.to_csv("PSA-SSA_Zips_Logic-Update2.csv", index=False)
print("Export complete.")