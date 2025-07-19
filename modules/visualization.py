"""
modules/visualization.py
────────────────────────
Streamlit ↔ pydeck helper for interactive visualisation of GeoJSON outputs
from Fatih’s archaeological RAG pipeline.

v0.5  (2025‑07‑17)
• Hexagon option now computes H3 bins on the CPU → tooltip shows point count
  & dominant category accurately.
• Column option lets the user choose height attribute (default = I_local).
• FutureWarnings from GeoPandas/pandas eliminated.
"""

from __future__ import annotations

from pathlib import Path
from math import log
from typing import Dict, Any, List

import geopandas as gpd
import h3
import pandas as pd
import pydeck as pdk
import streamlit as st
from shapely.geometry import MultiPoint, Polygon


# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

H3_RESOLUTION = 7      # ≈ 2–3 km hexagons near 18° N


# ------------------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------------------

def _hash_colour(value: str) -> List[int]:
    """Deterministically convert *value* to an RGBA list."""
    return [int(hash(value + s) % 256) for s in ("", "x", "y")] + [200]


def _colourize(df: pd.DataFrame, attr: str | None) -> Dict[str, List[int]]:
    """Add a ``fill_color`` column; return legend mapping."""
    if not attr:
        df["fill_color"] = [[30, 144, 255, 180]] * len(df)
        return {}
    uniq = sorted(df[attr].dropna().unique())
    cmap = {u: _hash_colour(str(u)) for u in uniq}
    df["fill_color"] = df[attr].map(lambda v: cmap.get(v, [128, 128, 128, 180]))
    return cmap


def _optimal_zoom(bounds: tuple[float, float, float, float]) -> int:
    minx, miny, maxx, maxy = bounds
    span = max(maxx - minx, maxy - miny)
    return max(1, min(int(8 - log(span + 1e-6, 2)), 15))


# ------------------------------------------------------------------------------
# Hexagon aggregation (CPU side)
# ------------------------------------------------------------------------------

def _h3_bin_dataframe(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Aggregate *gdf* to one row per H3 hexagon (resolution = H3_RESOLUTION).

    Returns
    -------
    GeoDataFrame with columns:
        n_points       – point count
        dom_cat        – dominant feature_Category
        I_local_mean   – mean I_local  (if present)
        sig95_sum      – sum of sig95 flags  (if present)
        geometry       – hexagon centroid
    """
    # 1 – assign H3 index
    gdf["_h3"] = [
        h3.geo_to_h3(lat, lon, H3_RESOLUTION)
        for lon, lat in zip(gdf["lon"], gdf["lat"])
    ]

    # 2 – aggregate statistics
    agg_dict: Dict[str, Any] = {
        "n_points": ("feature_Category", "size"),
        "dom_cat": ("feature_Category",
                    lambda s: s.mode().iloc[0] if not s.mode().empty else None),
    }
    if "I_local" in gdf.columns:
        agg_dict["I_local_mean"] = ("I_local", "mean")
    if "sig95" in gdf.columns:
        agg_dict["sig95_sum"] = ("sig95", "sum")

    agg = gdf.groupby("_h3").agg(**agg_dict).reset_index()

    # 3 – hexagon geometry (centroid) for plotting
    def _centroid(h):
        # geo_json=True -> [[lon, lat], …] already in the right order
        boundary = h3.h3_to_geo_boundary(h, geo_json=True)
        poly = Polygon(boundary)          # NO reversal!
        return poly.centroid


    agg["geometry"] = agg["_h3"].map(_centroid)
    gdf_agg = gpd.GeoDataFrame(agg, geometry="geometry", crs="EPSG:4326")
    gdf_agg["lon"] = gdf_agg.geometry.x
    gdf_agg["lat"] = gdf_agg.geometry.y
    return gdf_agg


# ------------------------------------------------------------------------------
# Layer factory
# ------------------------------------------------------------------------------

def _scatter_layer(df, **kw) -> pdk.Layer:
    return pdk.Layer(
        "ScatterplotLayer",
        data=df,
        get_position=["lon", "lat"],
        get_fill_color="fill_color",
        get_radius=kw.get("radius", 70),
        pickable=True,
        auto_highlight=True,
    )


def _heatmap_layer(df, **kw) -> pdk.Layer:
    return pdk.Layer(
        "HeatmapLayer",
        data=df,
        get_position=["lon", "lat"],
        get_weight=kw.get("weight", 1),
        radius_pixels=kw.get("radius_pixels", 60),
    )


def _hex_layer(df, **kw) -> pdk.Layer:
    # *df* is already aggregated – each row == one hexagon
    return pdk.Layer(
        "ColumnLayer",
        data=df,
        get_position=["lon", "lat"],
        get_elevation="n_points",
        elevation_scale=kw.get("elevation_scale", 40),
        radius=kw.get("radius", 250),
        get_fill_color="fill_color",
        pickable=True,
        auto_highlight=True,
    )


def _column_layer(df, **kw) -> pdk.Layer:
    return pdk.Layer(
        "ColumnLayer",
        data=df,
        get_position=["lon", "lat"],
        get_elevation=kw["height_attr"],
        elevation_scale=kw.get("elevation_scale", 900),
        radius=kw.get("radius", 45),
        get_fill_color="fill_color",
        pickable=True,
        auto_highlight=True,
    )


def _arc_layer(df, **kw) -> pdk.Layer:
    return pdk.Layer(
        "ArcLayer",
        data=df,
        get_source_position=["source_lon", "source_lat"],
        get_target_position=["target_lon", "target_lat"],
        get_source_color=[255, 0, 0],
        get_target_color=[0, 150, 255],
        get_width=kw.get("width", 2),
        pickable=True,
        auto_highlight=True,
    )


_LAYER_FACTORY = {
    "Scatterplot": _scatter_layer,
    "Heatmap": _heatmap_layer,
    # "Hexagon": _hex_layer,   # note: uses CPU‑aggregated dataframe
    # "Column": _column_layer,
    # "Arc": _arc_layer,
}

# Tooltip templates
_TOOLTIPS = {
    "Scatterplot": {
        "html": """
<b>Category</b>: {feature_Category}<br/>
<b>I<sub>local</sub></b>: {I_local}<br/>
<b>p</b>: {I_p}<br/>
<b>sig95</b>: {sig95}
""",
        "style": {"color": "white"},
    },
    "Hexagon": {
        "html": """
<b>N points</b>: {n_points}<br/>
<b>Dominant category</b>: {dom_cat}<br/>
<b>Mean I<sub>local</sub></b>: {I_local_mean:.2f}
""",
        "style": {"color": "white"},
    },
    "Column": {
        "html": """
<b>Category</b>: {feature_Category}<br/>
<b>I<sub>local</sub></b>: {I_local}<br/>
<b>sig95</b>: {sig95}
""",
        "style": {"color": "white"},
    },
}


# ------------------------------------------------------------------------------
# Public Streamlit helper
# ------------------------------------------------------------------------------

def show_kepler_map(folder: str = "results", preselect: str | None = None) -> None:
    files = list(Path(folder).rglob("*.geojson"))
    if not files:
        st.info("No GeoJSON files found.")
        return

    sel_file = st.selectbox(
        "🗂️ GeoJSON file",
        files,
        index=next((i for i, p in enumerate(files) if str(p) == preselect), 0)
        if preselect
        else 0,
    )

    # ── load ──────────────────────────────────────────────────────────
    try:
        gdf = gpd.read_file(sel_file)
        gdf = gdf.set_crs("EPSG:4326") if gdf.crs is None else gdf.to_crs("EPSG:4326")
    except Exception as exc:
        st.error(f"Could not read file: {exc}")
        return
    if gdf.empty:
        st.warning("File contains no geometries.")
        return

    # coordinates (loop avoids pandas FutureWarning)
    lon = []
    lat = []
    for geom in gdf.geometry.values:
        pt = geom if geom.geom_type == "Point" else geom.centroid
        lon.append(pt.x)
        lat.append(pt.y)
    gdf["lon"], gdf["lat"] = lon, lat

    # significance flag harmonisation
    if "_binary" in gdf.columns and "sig95" not in gdf.columns:
        gdf.rename(columns={"_binary": "sig95"}, inplace=True)

    # layer choices
    layer_choices = ["Scatterplot", "Heatmap", "Hexagon"]
    if "I_local" in gdf.columns:
        layer_choices.append("Column")
    if {"source_lon", "source_lat", "target_lon", "target_lat"}.issubset(gdf.columns):
        layer_choices.append("Arc")
    layer_type = st.selectbox("Darstellungsart", layer_choices)

    # colour mapping
    colour_attr = (
        st.selectbox(
            "Farben nach Attribut",
            [c for c in gdf.columns if gdf[c].dtype == "object" and gdf[c].nunique() < 50],
            index=0,
        )
        if layer_type not in {"Heatmap", "Arc"}
        else None
    )
    legend = _colourize(gdf, colour_attr)

    # special handling ---------------------------------------------------------
    layer_kwargs: Dict[str, Any] = {}

    if layer_type == "Hexagon":
        gdf = _h3_bin_dataframe(gdf)
        legend = _colourize(gdf, "dom_cat")  # colour by dominant category

    if layer_type == "Column":
        default_attr = "I_local" if "I_local" in gdf.columns else "sig95"
        height_attr = st.selectbox(
            "Höhe nach Attribut",
            [c for c in gdf.columns if gdf[c].dtype != "object"],
            index=gdf.columns.get_loc(default_attr),
        )
        layer_kwargs["height_attr"] = height_attr

    # build layer --------------------------------------------------------------
    layer = _LAYER_FACTORY[layer_type](gdf, **layer_kwargs)

    # view state ---------------------------------------------------------------
    bx = MultiPoint(gdf.geometry.values).bounds
    view = pdk.ViewState(
        latitude=(bx[1] + bx[3]) / 2,
        longitude=(bx[0] + bx[2]) / 2,
        zoom=_optimal_zoom(bx),
        pitch=45 if layer_type in {"Hexagon", "Column"} else 0,
    )

    # render -------------------------------------------------------------------
    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=view,
            tooltip=_TOOLTIPS.get(layer_type),
            map_provider="carto",
            map_style="dark",
        )
    )

    # legend -------------------------------------------------------------------
    if legend:
        st.markdown(f"### 🎨 Legend: {colour_attr if layer_type != 'Hexagon' else 'dom_cat'}")
        for val, rgba in legend.items():
            st.markdown(
                f'<div style="display:flex;align-items:center">'
                f'<div style="width:15px;height:15px;background-color:rgba({",".join(map(str, rgba))});margin-right:6px"></div>'
                f'{val}'
                f'</div>',
                unsafe_allow_html=True,
            )
