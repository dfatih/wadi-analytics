import streamlit as st
import pydeck as pdk
import geopandas as gpd
from shapely.geometry import MultiPoint
import pandas as pd
from pathlib import Path
import warnings
warnings.filterwarnings(
    "ignore",
    message="the convert_dtype parameter is deprecated and will be removed in a future version",
    category=FutureWarning,
    module="geopandas"
)


def show_kepler_map(folder: str = "results", preselect: str | None = None) -> None:
    folder_path = Path(folder)
    geojson_files = list(folder_path.rglob("*.geojson"))


    if not geojson_files:
        st.info("Keine GeoJSON-Dateien gefunden.")
        return

    selected_file = st.selectbox(
        "🗂️ GeoJSON-Datei auswählen",
        options=geojson_files,
        index=next((i for i, f in enumerate(geojson_files) if str(f) == preselect), 0)
        if preselect else 0
    )

    layer_type = st.selectbox("Darstellungsart", ["Scatterplot", "Heatmap", "Hexagon", "Column", "Arc"])

    try:
        gdf = gpd.read_file(selected_file)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        else:
            gdf = gdf.to_crs("EPSG:4326")
    except Exception as e:
        st.error(f"Fehler beim Laden:\n{e}")
        return

    if gdf.geometry.is_empty.all():
        st.warning("Keine Geometrie vorhanden.")
        return

    coords = gdf.geometry.apply(lambda g: (g.x, g.y) if g.geom_type == 'Point' else (g.centroid.x, g.centroid.y))
    print("GeomType breakdown:", gdf.geometry.geom_type.value_counts().to_dict())
    gdf["lon"] = [lon for lon, _ in coords]
    gdf["lat"] = [lat for _, lat in coords]

    # Kategorienfarben
    color_column = st.selectbox("Farben nach Attribut", [c for c in gdf.columns if gdf[c].nunique() < 50 and gdf[c].dtype == "object"], index=0) if layer_type != "Heatmap" else None
    if color_column:
        unique_vals = sorted(gdf[color_column].dropna().unique())
        palette = [
            [int((hash(v) % 256)), int((hash(v + 'x') % 256)), int((hash(v + 'y') % 256)), 180]
            for v in unique_vals
        ]
        color_map = dict(zip(unique_vals, palette))
        gdf["fill_color"] = gdf[color_column].apply(lambda v: color_map.get(v, [128, 128, 128, 180]))
    else:
        gdf["fill_color"] = [[30, 144, 255, 160]] * len(gdf)

    # Tooltip
    tooltip_fields = [col for col in gdf.columns if col not in {"geometry", "lon", "lat", "fill_color"}]
    tooltip_dict = {col: f"{{{col}}}" for col in tooltip_fields}

    bounds = MultiPoint(gdf.geometry.values).bounds
    lon_center = (bounds[0] + bounds[2]) / 2
    lat_center = (bounds[1] + bounds[3]) / 2
    view_state = pdk.ViewState(latitude=lat_center, longitude=lon_center, zoom=calculate_optimal_zoom(bounds), pitch=30)

    # Layer Auswahl
    if layer_type == "Scatterplot":
        layer = pdk.Layer(
            "ScatterplotLayer",
            data=gdf,
            get_position=["lon", "lat"],
            get_radius=80,
            get_fill_color="fill_color",
            pickable=True,
            auto_highlight=True,
        )
    elif layer_type == "Heatmap":
        layer = pdk.Layer(
            "HeatmapLayer",
            data=gdf,
            get_position=["lon", "lat"],
            get_weight=1,
            radius_pixels=60,
        )
    elif layer_type == "Hexagon":
        layer = pdk.Layer(
            "HexagonLayer",
            data=gdf,
            get_position=["lon", "lat"],
            radius=200,
            elevation_scale=50,
            elevation_range=[0, 1000],
            extruded=True,
            pickable=True,
        )
    
    elif layer_type == "Arc":
        arc_valid = {"source_lon", "source_lat", "target_lon", "target_lat"}.issubset(gdf.columns)
        if not arc_valid:
            st.warning("ArcLayer benötigt Spalten: source_lon, source_lat, target_lon, target_lat")
            return
        layer = pdk.Layer(
            "ArcLayer",
            data=gdf,
            get_source_position=["source_lon", "source_lat"],
            get_target_position=["target_lon", "target_lat"],
            get_source_color=[255, 0, 0],
            get_target_color=[0, 0, 255],
            pickable=True,
            auto_highlight=True,
        )
    else:
        st.error("Unbekannter Layer-Typ.")
        return

    # Karte rendern
    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=view_state,
            tooltip={"html": "<br>".join(f"<b>{k}</b>: {v}" for k, v in tooltip_dict.items())},
            map_style="dark",
        )
    )

    # Legende
    if color_column:
        st.markdown(f"### 🎨 Legende: {color_column}")
        for val in unique_vals:
            rgba = color_map[val]
            st.markdown(
                f'<div style="display:flex;align-items:center">'
                f'<div style="width:15px;height:15px;background-color:rgba({",".join(map(str, rgba))});margin-right:8px"></div>'
                f'{val}'
                f'</div>',
                unsafe_allow_html=True
            )


def calculate_optimal_zoom(bounds: tuple[float, float, float, float]) -> int:
    from math import log
    minx, miny, maxx, maxy = bounds
    spread = max(maxx - minx, maxy - miny)
    zoom = 8 - log(spread + 1e-6, 2)
    return max(1, min(int(zoom), 15))