from __future__ import annotations

import streamlit as st
import json
from modules.helper import run_cypher, run_python_code
from modules.llm import (
    extract_semantic_structure, 
    generate_analysis_code, 
    generate_cypher, 
    explain_cypher_result, 
    explain_de,
    extract_relevant_data, 
    decide_query_or_python
)
from pathlib import Path
from modules.logger import get_logger

logger = get_logger("debug")

def run_chat() -> None:
    """Run the conversational archaeology chatbot interface."""
    st.title("📜 Archaeology Chatbot")

    if "history" not in st.session_state:
        st.session_state.history = []

    user_input = st.chat_input("Frage stellen …")
    if not user_input:
        return

    st.session_state.history.append(("user", user_input))
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Analyse läuft …"):
            decisions = decide_query_or_python(user_input)

            if not decisions:
                st.error("❌ Keine gültige Analyse erkannt.")
                return

            for i, (decision_type, structure, analysis_type) in enumerate(decisions, start=1):
                st.markdown(f"---\n\n### 🔍 Analyse {i}: `{analysis_type}`")
                st.markdown(f"**Entscheidung:** `{decision_type}`")

                if decision_type == "cypher":
                    try:
                        query = generate_cypher(user_input)
                        rows = run_cypher(query)
                        preview = rows[:10] if isinstance(rows, list) else rows

                        st.subheader("📈 Ergebnis (Cypher-Vorschau)")
                        st.json(preview, expanded=False)

                        explanation = explain_cypher_result(user_input, rows)
                        st.markdown(explanation)

                        with st.expander("🧰️ Internals", expanded=False):
                            st.markdown("**Cypher:**")
                            st.code(query, language="cypher")
                    except Exception as e:
                        st.error(f"❌ Fehler bei Cypher-Ausführung: {e}")

                elif decision_type == "python":
                    try:
                        extract_relevant_data(user_input, structure=structure)
                    except Exception as e:
                        st.error(f"❌ Datenextraktion fehlgeschlagen: {e}")
                        continue

                    try:
                        analysis_outputs = generate_analysis_code(user_input, structure=structure, analysis_type=analysis_type)
                    except Exception as e:
                        st.error(f"❌ Codegenerierung fehlgeschlagen: {e}")
                        continue

                    current = next(
                        (o for o in analysis_outputs if isinstance(o, dict) and o.get("analysis_type") == analysis_type),
                        None
                    )
                    if not current:
                        st.warning(f"⚠️ Kein Code-Output für Typ `{analysis_type}` gefunden.")
                        continue

                    code = current["code"]
                    stdout, stderr = run_python_code(code)

                    if stdout:
                        st.subheader("💻 Python stdout")
                        st.code(stdout.strip(), language="text")

                    if stderr:
                        st.subheader("⚠️ Python stderr")
                        st.code(stderr.strip(), language="text")

                    geojson_files = list(Path("results").rglob(f"visualisierung/{analysis_type}/*.geojson"))
                    if geojson_files:
                        latest_geojson = max(geojson_files, key=lambda f: f.stat().st_mtime)
                        st.session_state["last_geojson"] = str(latest_geojson)

                    explanation = explain_de(user_input, stdout, stderr)
                    st.markdown(explanation)

                    with st.expander("🧰️ Internals", expanded=False):
                        st.markdown("**Python-Code:**")
                        st.code(code, language="python")

                else:
                    st.warning(f"❌ Unbekannter Entscheidungstyp: {decision_type}")