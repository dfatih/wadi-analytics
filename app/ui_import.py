"""Import pipeline: GeoPackage -> DuckDB -> Embeddings -> CSV -> Neo4j."""
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
# Configuration
# ---------------------------------------------------------------------------
GPKG_PATH = Path("data/WADI_12_2016.gpkg")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS") or os.getenv("NEO4J_PASSWORD", "")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run_import() -> None:
    """Runs the complete data import (4 steps)."""
    if not GPKG_PATH.exists():
        raise FileNotFoundError(f"GeoPackage not found: {GPKG_PATH}")

    st.write("### Step 1: Cleanup and DuckDB Import")
    with st.spinner("Cleaning data ..."):
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

        with st.expander("Removed site rows (missing X/Y)"):
            st.write(f"Total removed: {stats['dropped_sites_xy']}")
            st.dataframe(sites_dropped_xy.drop(columns=["geometry"]), use_container_width=True)

        with st.expander("Removed feature rows (missing X/Y)"):
            st.write(f"Total removed: {stats['dropped_feats_xy']}")
            st.dataframe(feats_dropped_xy.drop(columns=["geometry"]), use_container_width=True)

        with st.expander("Duplicate SiteIDs"):
            st.write(f"Total removed: {stats['dropped_sites_dup']}")
            st.dataframe(duplicate_sites.drop(columns=["geometry"]), use_container_width=True)

        with st.expander("Duplicate FeatureIDs"):
            st.write(f"Total removed: {stats['dropped_feats_dup']}")
            st.dataframe(duplicate_feats.drop(columns=["geometry"]), use_container_width=True)

        with st.expander("Orphaned features (no matching site)"):
                st.write(f"Total removed: {len(orphan_feats)}")
                st.dataframe(orphan_feats.drop(columns=["geometry"]), use_container_width=True)

    st.write("### Step 2: Generate Embeddings")
    with st.spinner("Generating embeddings ..."):
        generate_embeddings()
        st.success("Step 2 complete.")

    st.write("### Step 3: CSV Export")
    with st.spinner("Writing CSV files ..."):
        sites_csv, feats_csv = export_csvs()
        st.success(f"Exported: {sites_csv.name}, {feats_csv.name}")

    st.write("### Step 4: Neo4j Import")
    bar_sites = st.progress(0, text="Sites: 0%")
    bar_feats = st.progress(0, text="Features: 0%")
    bar_proximity = st.progress(0, text="Proximity: waiting ...")
    status_sites = st.empty()
    status_feats = st.empty()
    status_proximity = st.empty()

    total_sites = len(pd.read_csv(sites_csv))
    total_feats = len(pd.read_csv(feats_csv))

    def progress_cb(phase: str, processed: int, total: int):
        if phase == "sites":
            pct = min(int(processed / total_sites * 100), 100)
            text = f"Sites: {processed}/{total_sites} rows ({pct}%)"
            bar_sites.progress(pct, text=text)
            status_sites.text(text)
        elif phase == "feats":
            pct = min(int(processed / total_feats * 100), 100)
            text = f"Features: {processed}/{total_feats} rows ({pct}%)"
            bar_feats.progress(pct, text=text)
            status_feats.text(text)
        elif phase == "proximity":
            pct = min(int(processed / max(total, 1) * 100), 100)
            label = "Sites" if processed == 0 else ("Features" if processed == 1 else "done")
            text = f"Proximity: {label} ({pct}%)"
            bar_proximity.progress(pct, text=text)
            status_proximity.text(text)

    with st.spinner("Importing data into Neo4j ..."):
        import_to_neo4j(
            uri=NEO4J_URI,
            user=NEO4J_USER,
            password=NEO4J_PASS,
            sites_csv=sites_csv,
            feats_csv=feats_csv,
            batch_size=1000,
            progress_cb=progress_cb,
        )
        st.success("Import complete. Reload the page to switch to the chat.")

if __name__ == "__main__":
    run_import()
