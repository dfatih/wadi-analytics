from __future__ import annotations

import duckdb
import pandas as pd
import geopandas as gpd
from pathlib import Path
from shapely.geometry import Point
from typing import Union, BinaryIO
import tempfile
import io
import logging

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CACHE_PARQUET = Path("cache/parquet")
CACHE_DUCKDB = Path("cache/duckdb")
CACHE_PARQUET.mkdir(parents=True, exist_ok=True)
CACHE_DUCKDB.mkdir(parents=True, exist_ok=True)
DUCKDB_PATH = CACHE_DUCKDB / "archaeology.duckdb"

SITE_COLS = [
    "SiteID", "Category", "Location1", "Location2", "Surface", "NoOfFeatures",
    "X", "Y", "Shape_Length", "Shape_Area"
]

FEAT_COLS = [
    "FeatureID", "Site", "Category", "Location1", "Location2", "Length", "Width",
    "Height", "Condition", "Age", "X", "Y", "Category2", "RockArt1", "RockArt2",
    "RockArt3", "RockArt4", "RockArt5", "RockArt6"
]

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _materialise_stream(buf: BinaryIO) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".gpkg")
    tmp.write(buf.read())
    tmp.flush()
    return Path(tmp.name)

def _ensure_path(src: Union[str, Path, BinaryIO]) -> Path:
    if isinstance(src, (str, Path)):
        return Path(src)
    if isinstance(src, io.BufferedIOBase):
        return _materialise_stream(src)
    raise TypeError(f"Unsupported gpkg input type: {type(src).__name__}")

def _safe_reindex(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    return df.reindex(columns=cols)

def _coerce_float(df: pd.DataFrame, cols: list[str]) -> None:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Float64")

def _build_geom(df: pd.DataFrame, *, src_crs: str | None) -> gpd.GeoDataFrame:
    gdf = gpd.GeoDataFrame(df, geometry=[Point(xy) for xy in zip(df.X, df.Y)], crs=src_crs or "EPSG:32636")
    gdf_ll = gdf.to_crs(4326) if gdf.crs.to_epsg() != 4326 else gdf
    gdf["Lon"] = gdf_ll.geometry.x.round(6)
    gdf["Lat"] = gdf_ll.geometry.y.round(6)
    gdf["geometry"] = gdf["geometry"].apply(lambda g: g.wkt if g else None)
    return gdf

# ---------------------------------------------------------------------------
# Main Function
# ---------------------------------------------------------------------------
def gpkg_to_duckdb(gpkg: Union[str, Path, BinaryIO]) -> dict[str, int]:
    path = _ensure_path(gpkg)

    sites_raw = gpd.read_file(path, layer="Sites")
    feats_raw = gpd.read_file(path, layer="Features")

    drop_sites_xy = sites_raw[sites_raw[["X", "Y"]].isnull().any(axis=1)]
    drop_feats_xy = feats_raw[feats_raw[["X", "Y"]].isnull().any(axis=1)]
    drop_sites_dup = sites_raw[sites_raw.duplicated("SiteID", keep="first")]
    drop_feats_dup = feats_raw[feats_raw.duplicated("FeatureID", keep="first")]

    sites = sites_raw.dropna(subset=["X", "Y"]).drop_duplicates("SiteID", keep="first").copy()
    feats_clean = feats_raw.dropna(subset=["X", "Y"]).drop_duplicates("FeatureID", keep="first").copy()

    drop_feats_orphan = feats_clean[~feats_clean["Site"].isin(sites["SiteID"])]
    feats = feats_clean[feats_clean["Site"].isin(sites["SiteID"])].copy()

    _coerce_float(sites, ["X", "Y", "NoOfFeatures", "Shape_Length", "Shape_Area"])
    _coerce_float(feats, ["X", "Y", "Length", "Width", "Height", "Age"])

    src_crs_sites = sites_raw.crs.to_string() if getattr(sites_raw, "crs", None) else None
    src_crs_feats = feats_raw.crs.to_string() if getattr(feats_raw, "crs", None) else None

    sites = _build_geom(sites, src_crs=src_crs_sites)
    feats = _build_geom(feats, src_crs=src_crs_feats)

    sites.to_parquet(CACHE_PARQUET / "sites_clean.parquet", index=False)
    feats.to_parquet(CACHE_PARQUET / "features_clean.parquet", index=False)

    con = duckdb.connect(str(DUCKDB_PATH))
    con.register("sites", sites)
    con.register("feats", feats)
    con.execute(f"CREATE OR REPLACE TABLE Sites AS SELECT {', '.join(SITE_COLS + ['geometry', 'Lon', 'Lat'])} FROM sites")
    con.execute(f"CREATE OR REPLACE TABLE Features AS SELECT {', '.join(FEAT_COLS + ['geometry', 'Lon', 'Lat'])} FROM feats")
    con.close()

    return {
        "sites_total": len(sites_raw),
        "feats_total": len(feats_raw),
        "sites_valid": len(sites),
        "feats_valid": len(feats),
        "dropped_sites_xy": len(drop_sites_xy),
        "dropped_feats_xy": len(drop_feats_xy),
        "dropped_sites_dup": len(drop_sites_dup),
        "dropped_feats_dup": len(drop_feats_dup),
        "dropped_feats_orphan": len(drop_feats_orphan)
    }
