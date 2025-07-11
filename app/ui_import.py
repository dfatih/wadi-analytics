from __future__ import annotations

from pathlib import Path
from modules.neo4j.gpkg_to_duckdb import gpkg_to_duckdb
from modules.neo4j.generate_embeddings import generate_embeddings
from modules.neo4j.export_csv import export_csvs
from modules.neo4j.neo4j_import import import_to_neo4j
import os
import time
import streamlit as st
import pandas as pd
import geopandas as gpd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GPKG_PATH = Path("data/WADI_12_2016.gpkg")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS") or os.getenv("NEO4J_PASSWORD", "")

# ---------------------------------------------------------------------------
# UI Entry
# ---------------------------------------------------------------------------
def run_import() -> None:
    if not GPKG_PATH.exists():
        raise FileNotFoundError(f"Missing .gpkg file: {GPKG_PATH}")

    st.write("### 📦 Step 1: Cleaning and loading into DuckDB …")
    with st.spinner("Cleaning data …"):
        stats = gpkg_to_duckdb(GPKG_PATH)
        st.success("Step 1 complete.")

        sites_raw = gpd.read_file(GPKG_PATH, layer="Sites")
        feats_raw = gpd.read_file(GPKG_PATH, layer="Features")

        sites_dropped_xy = sites_raw[sites_raw[["X", "Y"]].isnull().any(axis=1)]
        feats_dropped_xy = feats_raw[feats_raw[["X", "Y"]].isnull().any(axis=1)]
        duplicate_sites = sites_raw[sites_raw.duplicated("SiteID", keep="first")]
        duplicate_feats = feats_raw[feats_raw.duplicated("FeatureID", keep="first")]
        orphan_feats = feats_raw[~feats_raw["Site"].isin(sites_raw["SiteID"])]

        for gdf in [sites_dropped_xy, feats_dropped_xy, duplicate_sites, duplicate_feats, orphan_feats]:
            if "geometry" in gdf.columns:
                gdf["geometry"] = gdf["geometry"].apply(lambda g: g.wkt if g else None)

        with st.expander("🧹 Dropped Site Rows (missing X/Y)"):
            st.write(f"Total dropped: {stats['dropped_sites_xy']}")
            st.dataframe(sites_dropped_xy.drop(columns=["geometry"]), use_container_width=True)

        with st.expander("🧹 Dropped Feature Rows (missing X/Y)"):
            st.write(f"Total dropped: {stats['dropped_feats_xy']}")
            st.dataframe(feats_dropped_xy.drop(columns=["geometry"]), use_container_width=True)

        with st.expander("⚠️ Duplicate SiteIDs"):
            st.write(f"Total dropped: {stats['dropped_sites_dup']}")
            st.dataframe(duplicate_sites.drop(columns=["geometry"]), use_container_width=True)

        with st.expander("⚠️ Duplicate FeatureIDs"):
            st.write(f"Total dropped: {stats['dropped_feats_dup']}")
            st.dataframe(duplicate_feats.drop(columns=["geometry"]), use_container_width=True)
        
        with st.expander("⚠️ Orphaned Features (no matching SiteID)"):
                st.write(f"Total dropped: {len(orphan_feats)}")
                st.dataframe(orphan_feats.drop(columns=["geometry"]), use_container_width=True)

    st.write("### 🔎 Step 2: Generating Embeddings …")
    with st.spinner("Generating embeddings …"):
        generate_embeddings()
        st.success("Step 2 complete.")

    st.write("### 📤 Step 3: Exporting CSVs …")
    with st.spinner("Writing final CSVs …"):
        sites_csv, feats_csv = export_csvs()
        st.success(f"Exported: {sites_csv.name}, {feats_csv.name}")

    st.write("### 📡 Step 4: Importing into Neo4j …")
    bar_sites = st.progress(0, text="Sites: 0%")
    bar_feats = st.progress(0, text="Features: 0%")
    status_sites = st.empty()
    status_feats = st.empty()

    total_sites = len(pd.read_csv(sites_csv))
    total_feats = len(pd.read_csv(feats_csv))

    def progress_cb(phase: str, processed: int, _ignored_total: int):
        total = total_sites if phase == "sites" else total_feats
        pct = min(int(processed / total * 100), 100)
        text = f"{phase.title()}: {processed}/{total} rows ({pct}%)"
        if phase == "sites":
            bar_sites.progress(pct, text=text)
            status_sites.text(text)
        elif phase == "feats":
            bar_feats.progress(pct, text=text)
            status_feats.text(text)

    with st.spinner("Importing into Neo4j …"):
        import_to_neo4j(
            uri=NEO4J_URI,
            user=NEO4J_USER,
            password=NEO4J_PASS,
            sites_csv=sites_csv,
            feats_csv=feats_csv,
            batch_size=1000,
            progress_cb=progress_cb,
        )
        st.success("✅ Import complete. Refresh the page to switch to chat mode.")

if __name__ == "__main__":
    run_import()
