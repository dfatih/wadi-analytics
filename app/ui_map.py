import streamlit as st
import modules.visualization as visualization



def show_map_view():
    st.title("🗺️ Geodaten-Visualisierung")

    default = st.session_state.get("last_geojson")

    st.markdown("Wähle eine Datei oder nutze die vom Chat übergebene.")

    visualization.show_kepler_map(preselect=default)
