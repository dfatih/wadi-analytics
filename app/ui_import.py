"""Import-Pipeline: GeoPackage -> DuckDB -> Embeddings -> CSV -> Neo4j."""
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
# Konfiguration
# ---------------------------------------------------------------------------
GPKG_PATH = Path("data/WADI_12_2016.gpkg")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS") or os.getenv("NEO4J_PASSWORD", "")

# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------
def run_import() -> None:
    """Fuehrt den vollstaendigen Datenimport durch (4 Schritte)."""
    if not GPKG_PATH.exists():
        raise FileNotFoundError(f"GeoPackage nicht gefunden: {GPKG_PATH}")

    st.write("### Schritt 1: Bereinigung und DuckDB-Import")
    with st.spinner("Daten werden bereinigt ..."):
        stats = gpkg_to_duckdb(GPKG_PATH)
        st.success("Schritt 1 abgeschlossen.")

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

        with st.expander("Entfernte Site-Zeilen (fehlende X/Y)"):
            st.write(f"Insgesamt entfernt: {stats['dropped_sites_xy']}")
            st.dataframe(sites_dropped_xy.drop(columns=["geometry"]), use_container_width=True)

        with st.expander("Entfernte Feature-Zeilen (fehlende X/Y)"):
            st.write(f"Insgesamt entfernt: {stats['dropped_feats_xy']}")
            st.dataframe(feats_dropped_xy.drop(columns=["geometry"]), use_container_width=True)

        with st.expander("Doppelte SiteIDs"):
            st.write(f"Insgesamt entfernt: {stats['dropped_sites_dup']}")
            st.dataframe(duplicate_sites.drop(columns=["geometry"]), use_container_width=True)

        with st.expander("Doppelte FeatureIDs"):
            st.write(f"Insgesamt entfernt: {stats['dropped_feats_dup']}")
            st.dataframe(duplicate_feats.drop(columns=["geometry"]), use_container_width=True)

        with st.expander("Verwaiste Features (kein zugehoeriger Site)"):
                st.write(f"Insgesamt entfernt: {len(orphan_feats)}")
                st.dataframe(orphan_feats.drop(columns=["geometry"]), use_container_width=True)

    st.write("### Schritt 2: Embeddings erzeugen")
    with st.spinner("Embeddings werden generiert ..."):
        generate_embeddings()
        st.success("Schritt 2 abgeschlossen.")

    st.write("### Schritt 3: CSV-Export")
    with st.spinner("CSV-Dateien werden geschrieben ..."):
        sites_csv, feats_csv = export_csvs()
        st.success(f"Exportiert: {sites_csv.name}, {feats_csv.name}")

    st.write("### Schritt 4: Neo4j-Import")
    bar_sites = st.progress(0, text="Sites: 0%")
    bar_feats = st.progress(0, text="Features: 0%")
    bar_proximity = st.progress(0, text="Proximity: wartend ...")
    status_sites = st.empty()
    status_feats = st.empty()
    status_proximity = st.empty()

    total_sites = len(pd.read_csv(sites_csv))
    total_feats = len(pd.read_csv(feats_csv))

    def progress_cb(phase: str, processed: int, total: int):
        if phase == "sites":
            pct = min(int(processed / total_sites * 100), 100)
            text = f"Sites: {processed}/{total_sites} Zeilen ({pct}%)"
            bar_sites.progress(pct, text=text)
            status_sites.text(text)
        elif phase == "feats":
            pct = min(int(processed / total_feats * 100), 100)
            text = f"Features: {processed}/{total_feats} Zeilen ({pct}%)"
            bar_feats.progress(pct, text=text)
            status_feats.text(text)
        elif phase == "proximity":
            pct = min(int(processed / max(total, 1) * 100), 100)
            label = "Sites" if processed == 0 else ("Features" if processed == 1 else "fertig")
            text = f"Proximity: {label} ({pct}%)"
            bar_proximity.progress(pct, text=text)
            status_proximity.text(text)

    with st.spinner("Daten werden in Neo4j importiert ..."):
        import_to_neo4j(
            uri=NEO4J_URI,
            user=NEO4J_USER,
            password=NEO4J_PASS,
            sites_csv=sites_csv,
            feats_csv=feats_csv,
            batch_size=1000,
            progress_cb=progress_cb,
        )
        st.success("Import abgeschlossen. Seite neu laden um zum Chat zu wechseln.")

if __name__ == "__main__":
    run_import()
