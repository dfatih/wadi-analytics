"""Map view for geodata visualisation.

Shows GeoJSON results from the chat analysis on an interactive map.
Lazy-imports modules.visualization to defer heavy geo dependencies
(geopandas, pydeck, h3) until the page is accessed.
"""
import streamlit as st


def show_map_view() -> None:
    """Renders the geodata visualisation."""
    import modules.visualization as visualization

    st.header("Geodata Visualisation")

    default = st.session_state.get("last_geojson")

    if default:
        st.caption("GeoJSON loaded from chat.")
    else:
        st.caption("Select a file or run an analysis in the chat first.")

    visualization.show_kepler_map(preselect=default)
