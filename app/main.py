"""Entry point for the Wadi-Analytics Streamlit application.

Configures theming, CSS injection, navigation via st.navigation()
and global sidebar controls (model selection, session metrics).
"""
from __future__ import annotations

import os

import streamlit as st
from neo4j import GraphDatabase, basic_auth

from chat_models import ChatMessage
from css import inject_css
from modules.helper import get_available_models, DEFAULT_MODEL
from modules.logger import get_logger
from ui_chat import run_chat
from ui_comparison import show_comparison_dashboard
from ui_import import run_import
from ui_map import show_map_view

log = get_logger(__name__)

# Neo4j connection from environment variables
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS") or os.getenv("NEO4J_PASSWORD", "")


# ---------------------------------------------------------------------------
# Neo4j check
# ---------------------------------------------------------------------------
def _neo4j_empty() -> bool:
    """Checks whether Neo4j contains any nodes.

    Returns True on connection errors so the import page is shown.
    """
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=basic_auth(NEO4J_USER, NEO4J_PASS))
        with driver.session() as session:
            total = session.run("MATCH (n) RETURN count(n) AS total").single()["total"]
        driver.close()
        return (total or 0) == 0
    except Exception as e:
        log.warning("Could not check Neo4j connectivity: %s", e)
        return True


# ---------------------------------------------------------------------------
# Session state initialisation and migration
# ---------------------------------------------------------------------------
def _init_session_state() -> None:
    """Initialises session state with defaults and migrates old formats."""
    # Migration: old (sender, text) tuples -> ChatMessage objects
    if "history" in st.session_state and "chat_messages" not in st.session_state:
        migrated: list[ChatMessage] = []
        for item in st.session_state["history"]:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                log.warning("Unknown history format skipped: %s", type(item))
                continue
            sender, text = item
            role = "user" if sender == "user" else "assistant"
            migrated.append(ChatMessage(role=role, text=str(text)))
        st.session_state["chat_messages"] = migrated
        del st.session_state["history"]

    st.session_state.setdefault("chat_messages", [])
    st.session_state.setdefault("active_model", DEFAULT_MODEL)
    st.session_state.setdefault("session_metrics", {
        "total_tokens": 0,
        "total_cost": 0.0,
        "n_queries": 0,
    })


# ---------------------------------------------------------------------------
# Import page (wrapper with double-start protection)
# ---------------------------------------------------------------------------
def _show_import_page() -> None:
    """Shows the import UI with a start button."""
    st.title("Wadi Abu Dom -- GeoImporter")
    st.caption("Selected: `data/WADI_12_2016.gpkg`")
    is_running = st.session_state.get("import_running", False)
    if st.button("Start Import", disabled=is_running):
        st.session_state["import_running"] = True
        try:
            run_import()
        finally:
            st.session_state["import_running"] = False


# ---------------------------------------------------------------------------
# Sidebar controls (global)
# ---------------------------------------------------------------------------
def _render_sidebar_controls() -> None:
    """Renders model selection, comparison toggle and session metrics in the sidebar."""
    with st.sidebar:
        # -- Model section --
        st.markdown(
            '<div class="sidebar-section-title">Model</div>',
            unsafe_allow_html=True,
        )

        models = get_available_models()
        if models:
            options = [m["api_name"] for m in models]
            labels = [m["display_name"] for m in models]

            current = st.session_state.get("active_model", DEFAULT_MODEL)
            current_idx = options.index(current) if current in options else 0

            idx = st.selectbox(
                "Model",
                range(len(options)),
                index=current_idx,
                format_func=lambda i: labels[i],
                key="model_selector_widget",
                label_visibility="collapsed",
            )
            st.session_state["active_model"] = options[idx]

        # -- Analysis section --
        st.markdown(
            '<div class="sidebar-section-title">Analysis</div>',
            unsafe_allow_html=True,
        )

        st.session_state["grid_cell_size"] = st.select_slider(
            "Grid Cell Size",
            options=[500, 1000, 2000, 5000, 10000],
            value=st.session_state.get("grid_cell_size", 2000),
            format_func=lambda x: f"{x} m",
            key="grid_cell_size_widget",
        )

        # Comparison mode
        st.toggle("Comparison Mode", key="comparison_toggle")

        if st.session_state.get("comparison_toggle"):
            if models:
                options = [m["api_name"] for m in models]
                labels_map = {m["api_name"]: m["display_name"] for m in models}
                selected = st.multiselect(
                    "Compare Models",
                    options,
                    default=options[:2],
                    format_func=lambda x: labels_map.get(x, x),
                    key="comparison_models_widget",
                )
                st.session_state["comparison_models_selected"] = selected
                if len(selected) < 2:
                    st.warning("Please select at least 2 models.")

        # -- Session section --
        st.markdown(
            '<div class="sidebar-section-title">Session</div>',
            unsafe_allow_html=True,
        )

        sm = st.session_state.get("session_metrics", {})
        n_queries = sm.get("n_queries", 0)
        total_cost = sm.get("total_cost", 0.0)

        col1, col2 = st.columns(2)
        col1.metric("Queries", n_queries)
        col2.metric("Cost", f"${total_cost:.3f}")

        if st.button("Clear Chat", use_container_width=True):
            st.session_state["chat_messages"] = []
            st.session_state["session_metrics"] = {
                "total_tokens": 0,
                "total_cost": 0.0,
                "n_queries": 0,
            }
            st.rerun()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(
        page_title="Wadi Abu Dom",
        page_icon=":material/landscape:",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    inject_css()
    _init_session_state()

    if _neo4j_empty():
        pg = st.navigation([st.Page(_show_import_page, title="Data Import")])
        pg.run()
        return

    _render_sidebar_controls()

    map_page = st.Page(show_map_view, title="Map", icon=":material/map:")
    pg = st.navigation({
        "Analysis": [
            st.Page(run_chat, title="Chat", icon=":material/chat:"),
            map_page,
        ],
        "Evaluation": [
            st.Page(show_comparison_dashboard, title="Model Comparison", icon=":material/compare:"),
        ],
    })

    # Programmatic page switch (triggered e.g. by "Show on Map")
    nav_target = st.session_state.pop("_navigate_to", None)
    if nav_target == "map":
        st.switch_page(map_page)

    pg.run()


if __name__ == "__main__":
    main()
