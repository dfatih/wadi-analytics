"""Karten-Ansicht fuer Geodaten-Visualisierung.

Zeigt GeoJSON-Ergebnisse aus der Chat-Analyse auf einer interaktiven Karte.
Lazy-Import von modules.visualization um schwere Geo-Abhaengigkeiten
(geopandas, pydeck, h3) erst beim Seitenwechsel zu laden.
"""
import streamlit as st


def show_map_view() -> None:
    """Rendert die Geodaten-Visualisierung."""
    import modules.visualization as visualization

    st.header("Geodaten-Visualisierung")

    default = st.session_state.get("last_geojson")

    if default:
        st.caption("GeoJSON vom Chat uebernommen.")
    else:
        st.caption("Waehle eine Datei oder fuehre zuerst eine Analyse im Chat durch.")

    visualization.show_kepler_map(preselect=default)
