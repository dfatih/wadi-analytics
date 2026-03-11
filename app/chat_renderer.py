"""Rendering-Logik fuer Chat-Nachrichten.

Rendert ChatMessage-Objekte aus der History mit Streamlit-Komponenten.
Reine Darstellung -- keine Side-Effects, keine Datenveraenderung.
"""
from __future__ import annotations

import json

import streamlit as st

from chat_models import ChatMessage, MetricsRecord, StepRecord
from css import _safe
from i18n import t


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------
def render_chat_message(msg: ChatMessage) -> None:
    """Rendert eine einzelne Chat-Nachricht (User oder Assistant)."""
    if msg.role == "user":
        with st.chat_message("user"):
            st.markdown(msg.text)
        return

    # Assistant-Nachricht
    with st.chat_message("assistant"):
        # Nur Text (z.B. migrierte Alt-Nachrichten oder Fehlermeldungen)
        if not msg.step_records and not msg.comparison_table:
            if msg.text:
                st.markdown(msg.text)
            if msg.metrics:
                _render_metrics_bar(msg.metrics)
            return

        # Plan-Uebersicht bei Multi-Step
        if len(msg.plan_steps) > 1:
            _render_plan_overview(msg.plan_steps)

        # Einzelne Schritte
        for record in msg.step_records:
            _render_step(record, msg.timestamp)

        # Finale Antwort des Agenten (nach den Schritten)
        if msg.text and msg.step_records:
            st.markdown(msg.text)

        # Vergleichstabelle (Comparison-Modus)
        if msg.is_comparison and msg.comparison_table:
            _render_comparison(msg.comparison_table)

        # Metriken-Badges (immer sichtbar, kompakt)
        if msg.metrics:
            _render_metrics_bar(msg.metrics)


# ---------------------------------------------------------------------------
# Plan-Uebersicht
# ---------------------------------------------------------------------------
def _render_plan_overview(plan_steps: list[dict]) -> None:
    """Rendert die Uebersicht eines Multi-Step-Analyseplans."""
    with st.expander(t("analysis_plan"), expanded=True):
        for step in plan_steps:
            dep = f" ({t('after_step')} {step['depends_on']})" if step.get("depends_on") is not None else ""
            cond = f" [{step['condition']}]" if step.get("condition", "none") != "none" else ""
            badge_cls = "badge-cypher" if step.get("decision_type") == "cypher" else "badge-python"

            st.markdown(
                f'<div class="plan-step">'
                f'<span class="step-num">{_safe(str(step["step_index"]))}.</span>'
                f'{_safe(step["sub_question"])} '
                f'<span class="step-type-badge {badge_cls}">{_safe(step["decision_type"])}</span> '
                f'<span class="step-meta">{_safe(step["analysis_type"])}{_safe(dep)}{_safe(cond)}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Einzelner Analyse-Schritt
# ---------------------------------------------------------------------------
def _render_step(record: StepRecord, msg_timestamp: str) -> None:
    """Rendert einen einzelnen Analyseschritt innerhalb einer Assistant-Nachricht."""
    if record.skipped:
        st.info(f"{t('step')} {record.step_index} {t('step_skipped')}: {record.skip_reason}")
        return

    # Step-Header mit Typ-Badge und Analyse-Typ-Label
    badge_cls = "badge-cypher" if record.decision_type == "cypher" else "badge-python"
    analysis_label = ""
    if record.analysis_type and record.analysis_type not in (record.decision_type, record.sub_question):
        analysis_label = (
            f' <span class="step-type-badge badge-analysis">'
            f'{_safe(record.analysis_type)}</span>'
        )
    # Tool-basierte Schritte: sub_question ist der Tool-Name -> schoener darstellen
    _TOOL_LABELS = {
        "run_cypher_query": t("data_query"),
        "run_spatial_analysis": t("spatial_analysis"),
    }
    header_text = _TOOL_LABELS.get(record.sub_question, record.sub_question)
    st.markdown(
        f'<div class="step-card">'
        f'<div class="step-header">'
        f'{t("step")} {record.step_index}: {_safe(header_text)} '
        f'<span class="step-type-badge {badge_cls}">{_safe(record.decision_type)}</span>'
        f'{analysis_label}'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # Erklaerung (immer sichtbar)
    if record.explanation:
        st.markdown(record.explanation)

    # Cypher-Vorschau
    if record.cypher_preview and record.decision_type == "cypher":
        with st.expander(t("result_preview"), expanded=False):
            try:
                st.json(json.loads(record.cypher_preview), expanded=False)
            except (json.JSONDecodeError, TypeError):
                st.code(record.cypher_preview, language="json")

    # stdout (falls vorhanden)
    if record.stdout and record.stdout.strip():
        with st.expander(t("output_stdout"), expanded=False):
            st.code(record.stdout.strip(), language="text")

    # stderr (nur bei echten Fehlern)
    if record.stderr and record.stderr.strip():
        if any(kw in record.stderr for kw in ["Traceback", "Error", "Exception"]):
            with st.expander(t("error_stderr"), expanded=False):
                st.code(record.stderr.strip(), language="text")

    # Internals (Code, Queries, Disambiguation)
    _render_internals(record)

    # "Auf Karte anzeigen"-Button
    if record.geojson_path:
        col1, col2 = st.columns([3, 1])
        with col1:
            from pathlib import Path
            st.caption(f"GeoJSON: {Path(record.geojson_path).name}")
        with col2:
            if st.button(
                t("show_on_map"),
                key=f"map_{msg_timestamp}_{record.step_index}",
            ):
                st.session_state["last_geojson"] = record.geojson_path
                st.session_state["_navigate_to"] = "map"
                st.rerun()


# ---------------------------------------------------------------------------
# Internals-Expander
# ---------------------------------------------------------------------------
def _render_internals(record: StepRecord) -> None:
    """Rendert den aufklappbaren Internals-Bereich eines Schritts."""
    has_content = (
        record.cypher_query
        or record.python_code
        or (record.disambiguation and (record.disambiguation.terms or record.disambiguation.notes))
    )
    if not has_content:
        return

    with st.expander(t("internals"), expanded=False):
        # Disambiguation
        if record.disambiguation:
            if record.disambiguation.terms:
                st.markdown(f"**{t('term_resolution')}**")
                for term in record.disambiguation.terms:
                    vals = ", ".join(f"`{v}`" for v in term.get("resolved_values", []))
                    st.markdown(
                        f"- *{term.get('original_text', '?')}* -> "
                        f"{term.get('node_type', '?')}.{term.get('property_name', '?')} = {vals} "
                        f"({term.get('confidence', '?')})"
                    )
            if record.disambiguation.notes:
                st.markdown(f"**{t('disambiguation')}**")
                for note in record.disambiguation.notes:
                    st.markdown(f"- {note}")

        # Code / Query
        if record.cypher_query:
            st.markdown(f"**{t('cypher')}**")
            st.code(record.cypher_query, language="cypher")
        if record.python_code:
            st.markdown(f"**{t('python_code')}**")
            st.code(record.python_code, language="python")


# ---------------------------------------------------------------------------
# Metriken-Badges
# ---------------------------------------------------------------------------
def _render_metrics_bar(metrics: MetricsRecord) -> None:
    """Rendert kompakte, immer sichtbare Metriken als Inline-Badges."""
    models = _safe(", ".join(metrics.models_used)) if metrics.models_used else "?"
    st.markdown(
        f'<div class="metrics-bar">'
        f'<span class="metric-badge">{t("tokens")} <span class="value">{metrics.total_tokens:,}</span></span>'
        f'<span class="metric-badge">{t("cost")} <span class="value">${metrics.cost_usd:.4f}</span></span>'
        f'<span class="metric-badge">{t("duration")} <span class="value">{metrics.duration_seconds:.1f}s</span></span>'
        f'<span class="metric-badge">{t("model")} <span class="value">{models}</span></span>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Vergleichstabelle
# ---------------------------------------------------------------------------
def _render_comparison(comparison_table: list[dict]) -> None:
    """Rendert die Vergleichstabelle und Charts fuer den Comparison-Modus."""
    st.markdown(f'<div class="comparison-header">{t("comparison_table")}</div>', unsafe_allow_html=True)

    table_data = []
    models = []
    tokens_list = []
    costs_list = []
    durations_list = []

    for row in comparison_table:
        m = row.get("metrics", {})
        model = row.get("model", "?")
        models.append(model)
        tokens_list.append(m.get("total_tokens", 0))
        costs_list.append(m.get("cost_usd", 0))
        durations_list.append(m.get("duration_seconds", 0))

        table_data.append({
            t("model"): model,
            t("success"): t("yes") if row.get("success") else t("no"),
            t("tokens"): m.get("total_tokens", 0),
            t("cost_usd"): f"{m.get('cost_usd', 0):.4f}",
            t("duration_s"): f"{m.get('duration_seconds', 0):.1f}",
        })

    st.table(table_data)

    # Balkendiagramme nebeneinander
    if len(models) >= 2:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f"**{t('tokens')}**")
            st.bar_chart(dict(zip(models, tokens_list)))
        with col2:
            st.markdown(f"**{t('cost_usd')}**")
            st.bar_chart(dict(zip(models, costs_list)))
        with col3:
            st.markdown(f"**{t('duration_s')}**")
            st.bar_chart(dict(zip(models, durations_list)))

    # Antworten nebeneinander
    if len(comparison_table) >= 2:
        st.markdown(f"**{t('answers_comparison')}**")
        cols = st.columns(len(comparison_table))
        for col, row in zip(cols, comparison_table):
            with col:
                st.markdown(f"**{row.get('model', '?')}**")
                explanation = row.get("explanation") or ""
                if not explanation and not row.get("success"):
                    stderr = row.get("stderr", "")
                    if stderr:
                        explanation = f"{t('analysis_failed')}\n\n`{stderr[:500]}`"
                    else:
                        explanation = t("analysis_failed_no_output")
                elif not explanation:
                    explanation = t("no_answer")
                st.markdown(explanation[:1000])


# ---------------------------------------------------------------------------
# Willkommensnachricht (leerer Chat)
# ---------------------------------------------------------------------------
def render_welcome() -> None:
    """Zeigt eine Willkommensnachricht wenn der Chat leer ist."""
    st.markdown(
        '<div class="welcome-box">'
        f"<h2>{t('welcome_title')}</h2>"
        f"<p>{t('welcome_text')}</p>"
        f"<p style='font-size: 0.82rem; margin-top: 1rem;'>"
        f"{t('welcome_examples')}"
        "</p></div>",
        unsafe_allow_html=True,
    )
