"""Einfache Internationalisierung fuer die Chat-UI.

Erkennt die Sprache der Benutzerfrage via langdetect und liefert
passende UI-Labels ueber die Funktion t(key).
Die erkannte Sprache wird in st.session_state["ui_lang"] gespeichert.

Spracherkennung selbst liegt in modules/language.py (Streamlit-frei).
"""
from __future__ import annotations

import streamlit as st
from modules.language import detect_language, language_name  # noqa: F401


def set_language(text: str) -> str:
    """Erkennt die Sprache und speichert sie in session_state. Gibt den Code zurueck."""
    lang = detect_language(text)
    st.session_state["ui_lang"] = lang
    return lang


def get_language() -> str:
    """Gibt die aktuelle UI-Sprache zurueck (Default: de)."""
    return st.session_state.get("ui_lang", "en")


# ---------------------------------------------------------------------------
# Label-Woerterbuch
# ---------------------------------------------------------------------------
_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "step": "Schritt",
        "data_query": "Datenabfrage",
        "spatial_analysis": "Raeumliche Analyse",
        "analysis_plan": "Analyseplan",
        "result_preview": "Ergebnisvorschau",
        "output_stdout": "Ausgabe (stdout)",
        "error_stderr": "Fehlerausgabe (stderr)",
        "internals": "Internals",
        "term_resolution": "Begriffsaufloesung:",
        "disambiguation": "Disambiguierung:",
        "cypher": "Cypher:",
        "python_code": "Python-Code:",
        "show_on_map": "Auf Karte anzeigen",
        "tokens": "Tokens",
        "cost": "Kosten",
        "duration": "Dauer",
        "model": "Modell",
        "comparison_table": "Vergleichstabelle",
        "success": "Erfolg",
        "yes": "Ja",
        "no": "Nein",
        "cost_usd": "Kosten ($)",
        "duration_s": "Dauer (s)",
        "answers_comparison": "Antworten im Vergleich:",
        "analysis_failed": "Analyse fehlgeschlagen:",
        "analysis_failed_no_output": "Analyse fehlgeschlagen (keine Ausgabe).",
        "no_answer": "Keine Antwort.",
        "step_skipped": "uebersprungen",
        "after_step": "nach Schritt",
        "ask_question": "Frage stellen ...",
        "comparison_running": "Modellvergleich wird durchgefuehrt...",
        "comparison_done": "Vergleich abgeschlossen",
        "analysis_running": "Analyse wird durchgefuehrt...",
        "analysis_done": "Analyse abgeschlossen",
        "select_2_models": "Bitte mindestens 2 Modelle im Vergleichsmodus auswaehlen.",
        "comparison_for": "Modellvergleich fuer:",
        "welcome_title": "Wadi Abu Dom",
        "welcome_text": "Archaeologische Analyse -- stelle eine Frage um zu beginnen.",
        "welcome_examples": (
            "Beispiele: <em>Welche Feature-Kategorien gibt es?</em> | "
            "<em>Gibt es raeumliche Cluster bei Graebern?</em> | "
            "<em>Wie verteilen sich Siedlungen entlang des Wadi?</em>"
        ),
        "truncated": "gekuerzt, {n} Zeichen gesamt",
        "layer_type": "Darstellungsart",
        "colour_by": "Farben nach Attribut",
        "no_colour_options": "Keine faerbbaren Attribute verfuegbar.",
        "height_by": "Hoehe nach Attribut",
        "legend": "Legende",
    },
    "en": {
        "step": "Step",
        "data_query": "Data Query",
        "spatial_analysis": "Spatial Analysis",
        "analysis_plan": "Analysis Plan",
        "result_preview": "Result Preview",
        "output_stdout": "Output (stdout)",
        "error_stderr": "Error Output (stderr)",
        "internals": "Internals",
        "term_resolution": "Term Resolution:",
        "disambiguation": "Disambiguation:",
        "cypher": "Cypher:",
        "python_code": "Python Code:",
        "show_on_map": "Show on Map",
        "tokens": "Tokens",
        "cost": "Cost",
        "duration": "Duration",
        "model": "Model",
        "comparison_table": "Comparison Table",
        "success": "Success",
        "yes": "Yes",
        "no": "No",
        "cost_usd": "Cost ($)",
        "duration_s": "Duration (s)",
        "answers_comparison": "Answers Comparison:",
        "analysis_failed": "Analysis failed:",
        "analysis_failed_no_output": "Analysis failed (no output).",
        "no_answer": "No answer.",
        "step_skipped": "skipped",
        "after_step": "after step",
        "ask_question": "Ask a question ...",
        "comparison_running": "Running model comparison...",
        "comparison_done": "Comparison complete",
        "analysis_running": "Running analysis...",
        "analysis_done": "Analysis complete",
        "select_2_models": "Please select at least 2 models for comparison mode.",
        "comparison_for": "Model comparison for:",
        "welcome_title": "Wadi Abu Dom",
        "welcome_text": "Archaeological Analysis -- ask a question to begin.",
        "welcome_examples": (
            "Examples: <em>What feature categories exist?</em> | "
            "<em>Are there spatial clusters among graves?</em> | "
            "<em>How are settlements distributed along the Wadi?</em>"
        ),
        "truncated": "truncated, {n} characters total",
        "layer_type": "Layer Type",
        "colour_by": "Colour by Attribute",
        "no_colour_options": "No colourable attributes available.",
        "height_by": "Height by Attribute",
        "legend": "Legend",
    },
}


def t(key: str) -> str:
    """Gibt das Label fuer den aktuellen Sprachkontext zurueck.

    Fuer Sprachen ohne eigenes Label-Set wird Englisch als Fallback verwendet.
    """
    lang = get_language()
    fallback = _LABELS["en"]
    labels = _LABELS.get(lang, fallback)
    return labels.get(key, fallback.get(key, key))
