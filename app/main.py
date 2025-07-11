# main.py

import streamlit as st
from neo4j import GraphDatabase, basic_auth
import os
from modules.logger import get_logger
from ui_import import run_import   # calls ui_import.main()
from ui_chat import run_chat             # calls your chat entrypoint
from ui_map import show_map_view

log = get_logger(__name__)

# Pull credentials from env (same vars used by ui_import.py)
NEO4J_URI  = os.getenv("NEO4J_URI",  "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS") or os.getenv("NEO4J_PASSWORD", "")

# ---------------------------------------------------------------------------
def _neo4j_empty() -> bool:
    """
    Return True if Neo4j has zero nodes. If any exception occurs
    (e.g. bad creds, network issues), treat it as “empty” so the
    import UI can surface for the user to fix credentials.
    """
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=basic_auth(NEO4J_USER, NEO4J_PASS))
        with driver.session() as session:
            total = session.run("MATCH (n) RETURN count(n) AS total").single()["total"]
        driver.close()
        return (total or 0) == 0
    except Exception as e:
        log.warning("Could not check Neo4j emptiness: %s", e)
        return True

# ---------------------------------------------------------------------------
def main() -> None:
    # This must come first before any other Streamlit commands:
    st.set_page_config(page_title="Wadi Abu Dom", layout="wide")

    if _neo4j_empty():
        # Database is empty or unreachable → show import screen
        st.title("Wadi Abu Dom – GeoImporter")
        st.caption("Selected: `data/WADI_12_2016.gpkg`")
        
        if st.button("🚀 Start Import"):
            run_import()
    else:
        page = st.sidebar.radio("📚 Navigation", ["🧠 Chat", "🗺️ Karte"])
        if page == "🧠 Chat":
            run_chat()
        elif page == "🗺️ Karte":
            show_map_view()

if __name__ == "__main__":
    main()
