"""Zentralisiertes CSS fuer die Wadi-Analytics-Oberflaeche.

Wird einmalig beim App-Start via st.markdown(GLOBAL_CSS, unsafe_allow_html=True)
injiziert.  Alle Custom-Styles an einer Stelle -- keine verstreuten Style-Bloecke.
"""
import html as _html

import streamlit as st


def _safe(text: str) -> str:
    """Escaped dynamische Texte fuer sichere HTML-Interpolation."""
    return _html.escape(str(text), quote=True)


# ---------------------------------------------------------------------------
# Farb-Tokens (konsistent mit .streamlit/config.toml)
# ---------------------------------------------------------------------------
PRIMARY = "#D4A853"
BG_DARK = "#0E1117"
BG_CARD = "#1A1D26"
TEXT_PRIMARY = "#FAFAFA"
TEXT_MUTED = "rgba(250, 250, 250, 0.5)"
TEXT_DIM = "rgba(250, 250, 250, 0.4)"
BORDER_SUBTLE = "rgba(255, 255, 255, 0.08)"
CYPHER_COLOR = "#4DA8FF"
PYTHON_COLOR = "#50C878"

GLOBAL_CSS = f"""
<style>
/* ================================================================
   1. CHAT-NACHRICHTEN -- kompaktere Abstande
   ================================================================ */
.stChatMessage {{
    padding-top: 0.4rem !important;
    padding-bottom: 0.4rem !important;
}}

/* ================================================================
   2. METRIKEN-BADGES (inline, immer sichtbar)
   ================================================================ */
.metrics-bar {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    padding: 6px 0;
    margin-top: 8px;
    border-top: 1px solid {BORDER_SUBTLE};
}}
.metric-badge {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 10px;
    border-radius: 12px;
    background: rgba(255, 255, 255, 0.06);
    font-size: 0.78rem;
    color: {TEXT_MUTED};
    white-space: nowrap;
}}
.metric-badge .value {{
    font-weight: 600;
    color: {PRIMARY};
}}

/* ================================================================
   3. STEP-CARDS (Analyse-Schritte)
   ================================================================ */
.step-card {{
    border-left: 3px solid rgba(212, 168, 83, 0.4);
    padding: 8px 12px;
    margin: 8px 0;
    border-radius: 0 8px 8px 0;
    background: rgba(255, 255, 255, 0.02);
}}
.step-header {{
    font-size: 0.85rem;
    color: {TEXT_MUTED};
    margin-bottom: 4px;
}}
.step-type-badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
.badge-cypher {{
    background: rgba(0, 150, 255, 0.15);
    color: {CYPHER_COLOR};
}}
.badge-python {{
    background: rgba(80, 200, 120, 0.15);
    color: {PYTHON_COLOR};
}}
.badge-analysis {{
    background: rgba(212, 168, 83, 0.15);
    color: {PRIMARY};
    font-style: italic;
}}

/* ================================================================
   4. SIDEBAR-SEKTIONEN
   ================================================================ */
.sidebar-section-title {{
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: {TEXT_DIM};
    margin: 1.2rem 0 0.5rem 0;
    padding-bottom: 4px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}}

/* ================================================================
   5. WILLKOMMENSNACHRICHT (leerer Chat)
   ================================================================ */
.welcome-box {{
    text-align: center;
    padding: 3rem 2rem;
    color: {TEXT_MUTED};
}}
.welcome-box h2 {{
    color: {PRIMARY};
    margin-bottom: 0.5rem;
}}

/* ================================================================
   6. PLAN-UEBERSICHT
   ================================================================ */
.plan-step {{
    padding: 4px 0;
    font-size: 0.88rem;
    color: {TEXT_PRIMARY};
}}
.plan-step .step-num {{
    font-weight: 700;
    color: {PRIMARY};
    margin-right: 6px;
}}
.plan-step .step-meta {{
    font-size: 0.78rem;
    color: {TEXT_MUTED};
}}

/* ================================================================
   7. VERGLEICHSTABELLE
   ================================================================ */
.comparison-header {{
    font-size: 0.9rem;
    font-weight: 600;
    color: {PRIMARY};
    padding: 8px 0 4px 0;
}}

/* ================================================================
   8. GLOBALE ANPASSUNGEN
   ================================================================ */
#MainMenu {{ visibility: hidden; }}
footer {{ visibility: hidden; }}

/* Expander-Headers dezenter */
.streamlit-expanderHeader {{
    font-size: 0.85rem !important;
    color: {TEXT_MUTED} !important;
}}
</style>
"""


def inject_css() -> None:
    """Injiziert das globale CSS in die Streamlit-Seite."""
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)
