"""Chat-Oberflaeche fuer den Archaeologie-Chatbot.

Unterstuetzt normalen Einzel-Modell-Modus mit agentischem Tool-Use
sowie einen Modellvergleichsmodus fuer Seite-an-Seite-Bewertung.

Architektur:
- run_agent() in modules/llm.py fuehrt den agentischen LLM-Loop aus
- AgentResult wird auf ChatMessage/StepRecord gemappt
- Rendering erfolgt ueber chat_renderer.py
- Chat-History wird in session_state["chat_messages"] als ChatMessage-Objekte gespeichert
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from chat_models import (
    ChatMessage,
    DisambiguationRecord,
    MetricsRecord,
    StepRecord,
    enforce_turn_limit,
)
from chat_renderer import render_chat_message, render_welcome
from modules.disambiguator import drain_disambiguation_results
from modules.helper import (
    drain_llm_results,
    AgentResult,
)
from modules.llm import run_agent
from modules.logger import get_logger

logger = get_logger("debug")

COMPARISON_DIR = Path("results") / "comparisons"
QUERIES_DIR = Path("results") / "queries"


# ---------------------------------------------------------------------------
# Mapping: AgentResult -> ChatMessage
# ---------------------------------------------------------------------------
def _collect_disambiguation() -> DisambiguationRecord:
    """Sammelt Disambiguierungs-Ergebnisse und gibt sie als Record zurueck."""
    all_resolved = drain_disambiguation_results()
    seen_terms: set[tuple] = set()
    terms: list[dict] = []
    for rq in all_resolved:
        for t in rq.terms:
            key = (t.original_text, t.node_type, t.property_name, tuple(t.resolved_values))
            if key in seen_terms:
                continue
            seen_terms.add(key)
            terms.append({
                "original_text": t.original_text,
                "node_type": t.node_type,
                "property_name": t.property_name,
                "resolved_values": t.resolved_values,
                "confidence": t.confidence,
            })
    seen_notes: set[str] = set()
    notes: list[str] = []
    for rq in all_resolved:
        for n in rq.disambiguation_notes:
            if n not in seen_notes:
                seen_notes.add(n)
                notes.append(n)
    return DisambiguationRecord(terms=terms, notes=notes)



def _agent_result_to_message(result: AgentResult, timestamp: str) -> ChatMessage:
    """Konvertiert ein AgentResult in eine ChatMessage fuer die History."""
    msg = ChatMessage(role="assistant", timestamp=timestamp)
    msg.text = result.answer

    # Disambiguation sammeln
    disamb = _collect_disambiguation()

    # Tool-Aufrufe auf StepRecords mappen
    for i, tc in enumerate(result.tool_calls):
        decision_type = "cypher" if tc.tool_name == "run_cypher_query" else "python"
        analysis_type = tc.arguments.get("analysis_type", tc.tool_name)

        record = StepRecord(
            step_index=i + 1,
            analysis_type=analysis_type,
            decision_type=decision_type,
            sub_question=tc.tool_name,
            success=tc.success,
            explanation="",
            stdout=tc.stdout,
            stderr=tc.stderr,
            cypher_query=tc.cypher_query,
            python_code=tc.python_code,
            geojson_path=tc.geojson_path,
            disambiguation=disamb if i == 0 else None,
        )
        msg.step_records.append(record)

        # GeoJSON fuer Karte verfuegbar machen
        if tc.geojson_path:
            st.session_state["last_geojson"] = tc.geojson_path

    # Metriken aus AgentResult direkt bauen
    msg.metrics = MetricsRecord(
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        reasoning_tokens=result.reasoning_tokens,
        total_tokens=result.total_tokens,
        cost_usd=result.cost_usd,
        duration_seconds=result.duration_seconds,
        models_used=[result.model],
    )

    return msg


# ---------------------------------------------------------------------------
# Normaler Chat-Modus (einzelnes Modell, agentischer Loop)
# ---------------------------------------------------------------------------
def _run_normal_mode(user_input: str, selected_model: str) -> ChatMessage:
    """Fuehrt den agentischen LLM-Loop aus und gibt eine ChatMessage zurueck."""
    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    drain_llm_results()
    drain_disambiguation_results()

    cell_size = st.session_state.get("grid_cell_size", 2000)
    result = run_agent(user_input, model=selected_model, cell_size=cell_size)

    return _agent_result_to_message(result, now)


# ---------------------------------------------------------------------------
# Vergleichsmodus (mehrere Modelle)
# ---------------------------------------------------------------------------
def _run_comparison_mode(user_input: str, selected_models: list[str]) -> ChatMessage:
    """Fuehrt dieselbe Frage durch mehrere Modelle und gibt eine ChatMessage zurueck."""
    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    msg = ChatMessage(role="assistant", timestamp=now, is_comparison=True)
    msg.text = f"Modellvergleich fuer: {user_input}"

    comparison_rows: list[dict] = []

    for model_name in selected_models:
        drain_llm_results()
        drain_disambiguation_results()

        data_path = f"results/comparison_{model_name}_input.json"
        cell_size = st.session_state.get("grid_cell_size", 2000)
        result = run_agent(user_input, model=model_name, data_path=data_path, cell_size=cell_size)

        # Ergebnisse sammeln
        cypher_query = ""
        python_code = ""
        stdout = ""
        stderr = ""
        geojson_path = ""
        summary_json = None
        analysis_type = ""

        for tc in result.tool_calls:
            if tc.tool_name == "run_cypher_query":
                cypher_query = tc.cypher_query
            elif tc.tool_name == "run_spatial_analysis":
                python_code = tc.python_code
                analysis_type = tc.arguments.get("analysis_type", "")
                geojson_path = tc.geojson_path or geojson_path
                summary_json = tc.summary_json or summary_json
            stdout += tc.stdout + "\n"
            stderr += tc.stderr + "\n"

        success = all(tc.success for tc in result.tool_calls) if result.tool_calls else bool(result.answer)

        comparison_rows.append({
            "model": model_name,
            "success": success,
            "analysis_type": analysis_type,
            "decision_type": "agent",
            "explanation": result.answer,
            "cypher_query": cypher_query,
            "python_code": python_code,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
            "summary_json": summary_json,
            "geojson_path": geojson_path,
            "metrics": {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "reasoning_tokens": result.reasoning_tokens,
                "total_tokens": result.total_tokens,
                "cost_usd": result.cost_usd,
                "duration_seconds": result.duration_seconds,
            },
        })

    msg.comparison_table = comparison_rows

    # Gesamt-Metriken
    msg.metrics = MetricsRecord(
        prompt_tokens=sum(r["metrics"]["prompt_tokens"] for r in comparison_rows),
        completion_tokens=sum(r["metrics"]["completion_tokens"] for r in comparison_rows),
        reasoning_tokens=sum(r["metrics"]["reasoning_tokens"] for r in comparison_rows),
        total_tokens=sum(r["metrics"]["total_tokens"] for r in comparison_rows),
        cost_usd=round(sum(r["metrics"]["cost_usd"] for r in comparison_rows), 6),
        duration_seconds=round(sum(r["metrics"]["duration_seconds"] for r in comparison_rows), 2),
        models_used=selected_models,
    )

    # Persistieren
    if comparison_rows:
        _persist_comparison(user_input, comparison_rows)

    return msg


# ---------------------------------------------------------------------------
# Vergleichs-Persistenz
# ---------------------------------------------------------------------------
def _persist_comparison(question: str, comparison_rows: list[dict]) -> Path:
    """Speichert Vergleichsergebnisse als JSON und CSV."""
    COMPARISON_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    json_path = COMPARISON_DIR / f"{ts}.json"
    first = comparison_rows[0] if comparison_rows else {}
    record = {
        "timestamp": ts,
        "question": question,
        "analysis_type": first.get("analysis_type", ""),
        "decision_type": first.get("decision_type", ""),
        "results": comparison_rows,
    }
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)

    csv_path = COMPARISON_DIR / f"{ts}.csv"
    _persist_comparison_csv(csv_path, question, comparison_rows)

    logger.info("Vergleich persistiert: %s (.json + .csv)", ts)
    return json_path


def _persist_comparison_csv(path: Path, question: str, rows: list[dict]) -> None:
    """Schreibt eine flache CSV mit einer Zeile pro Modell."""
    fieldnames = [
        "question", "model", "success", "analysis_type", "decision_type",
        "prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens",
        "cost_usd", "duration_seconds",
        "moran_I", "p_value", "n",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            m = row.get("metrics", {})
            sj = row.get("summary_json") or {}
            moran_i = sj.get("moran_I")
            p_value = sj.get("p_value")
            n = sj.get("n")
            if moran_i is None:
                for v in sj.values():
                    if isinstance(v, dict) and "moran_I" in v:
                        moran_i = v.get("moran_I")
                        p_value = v.get("p_value")
                        n = v.get("n")
                        break
            writer.writerow({
                "question": question,
                "model": row.get("model", ""),
                "success": row.get("success", False),
                "analysis_type": row.get("analysis_type", ""),
                "decision_type": row.get("decision_type", ""),
                "prompt_tokens": m.get("prompt_tokens", 0),
                "completion_tokens": m.get("completion_tokens", 0),
                "reasoning_tokens": m.get("reasoning_tokens", 0),
                "total_tokens": m.get("total_tokens", 0),
                "cost_usd": m.get("cost_usd", 0),
                "duration_seconds": m.get("duration_seconds", 0),
                "moran_I": moran_i if moran_i is not None else "",
                "p_value": p_value if p_value is not None else "",
                "n": n if n is not None else "",
            })


# ---------------------------------------------------------------------------
# Query-Persistenz (nach jeder Frage)
# ---------------------------------------------------------------------------
def _persist_query_result(question: str, assistant_msg: ChatMessage) -> Path:
    """Speichert das vollstaendige Ergebnis einer Frage als JSON.

    Erfasst Frage, Antwort, alle Schritte mit stdout/stderr/Code,
    Metriken, Modell, Grid-Zellgroesse und Analyse-Typ.
    """
    QUERIES_DIR.mkdir(parents=True, exist_ok=True)
    ts = assistant_msg.timestamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    # Analyse-Typ aus den Schritten extrahieren
    analysis_type = ""
    for rec in assistant_msg.step_records:
        if rec.analysis_type and rec.analysis_type not in ("run_cypher_query", "run_spatial_analysis"):
            analysis_type = rec.analysis_type
            break

    # Schritte serialisieren
    steps = []
    for rec in assistant_msg.step_records:
        step_data: dict = {
            "step_index": rec.step_index,
            "tool_name": rec.sub_question,
            "analysis_type": rec.analysis_type,
            "decision_type": rec.decision_type,
            "success": rec.success,
            "stdout": rec.stdout,
            "stderr": rec.stderr,
        }
        if rec.cypher_query:
            step_data["cypher_query"] = rec.cypher_query
        if rec.python_code:
            step_data["python_code"] = rec.python_code
        if rec.geojson_path:
            step_data["geojson_path"] = rec.geojson_path
        if rec.cypher_preview:
            step_data["cypher_preview"] = rec.cypher_preview
        if rec.explanation:
            step_data["explanation"] = rec.explanation
        if rec.disambiguation and (rec.disambiguation.terms or rec.disambiguation.notes):
            step_data["disambiguation"] = {
                "terms": rec.disambiguation.terms,
                "notes": rec.disambiguation.notes,
            }
        steps.append(step_data)

    # Metriken
    metrics_data = {}
    if assistant_msg.metrics:
        m = assistant_msg.metrics
        metrics_data = {
            "prompt_tokens": m.prompt_tokens,
            "completion_tokens": m.completion_tokens,
            "reasoning_tokens": m.reasoning_tokens,
            "total_tokens": m.total_tokens,
            "cost_usd": m.cost_usd,
            "duration_seconds": m.duration_seconds,
            "models_used": m.models_used,
        }

    record = {
        "timestamp": ts,
        "question": question,
        "answer": assistant_msg.text or "",
        "model": metrics_data.get("models_used", [""])[0] if metrics_data else "",
        "grid_cell_size": st.session_state.get("grid_cell_size", 2000),
        "analysis_type": analysis_type,
        "is_comparison": assistant_msg.is_comparison,
        "success": all(rec.success for rec in assistant_msg.step_records) if assistant_msg.step_records else bool(assistant_msg.text),
        "n_steps": len(steps),
        "steps": steps,
        "metrics": metrics_data,
    }

    # Comparison-Tabelle einbetten falls vorhanden
    if assistant_msg.comparison_table:
        record["comparison_table"] = assistant_msg.comparison_table

    json_path = QUERIES_DIR / f"{ts}.json"
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)

    logger.info("Query-Ergebnis persistiert: %s", json_path.name)
    return json_path


# ---------------------------------------------------------------------------
# Session-Metriken
# ---------------------------------------------------------------------------
def _update_session_metrics(metrics: MetricsRecord | None) -> None:
    """Aktualisiert die aggregierten Session-Metriken in session_state."""
    if not metrics:
        return
    sm = st.session_state.get("session_metrics", {})
    sm["total_tokens"] = sm.get("total_tokens", 0) + metrics.total_tokens
    sm["total_cost"] = sm.get("total_cost", 0.0) + metrics.cost_usd
    sm["n_queries"] = sm.get("n_queries", 0) + 1
    st.session_state["session_metrics"] = sm


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------
def run_chat() -> None:
    """Startet die Chat-Oberflaeche fuer archaeologische Analysen."""
    messages: list[ChatMessage] = st.session_state.get("chat_messages", [])

    # Willkommensnachricht bei leerem Chat
    if not messages:
        render_welcome()

    # Alle historischen Nachrichten rendern
    for msg in messages:
        render_chat_message(msg)

    # Neue Eingabe verarbeiten
    user_input = st.chat_input("Frage stellen ...")
    if not user_input:
        return

    # User-Nachricht speichern
    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    user_msg = ChatMessage(role="user", timestamp=now, text=user_input)
    st.session_state["chat_messages"].append(user_msg)

    # User-Nachricht sofort rendern
    with st.chat_message("user"):
        st.markdown(user_input)

    # Modell-Einstellungen aus Sidebar (von main.py gesetzt)
    comparison_mode = st.session_state.get("comparison_toggle", False)
    selected_model = st.session_state.get("active_model", "gpt-4.1")

    # Ausfuehrung mit Live-Fortschritt
    with st.chat_message("assistant"):
        if comparison_mode:
            selected_models = st.session_state.get("comparison_models_selected", [])
            if not selected_models or len(selected_models) < 2:
                st.warning("Bitte mindestens 2 Modelle im Vergleichsmodus auswaehlen.")
                return
            with st.status("Modellvergleich wird durchgefuehrt...", expanded=True) as status:
                assistant_msg = _run_comparison_mode(user_input, selected_models)
                status.update(label="Vergleich abgeschlossen", state="complete", expanded=False)
        else:
            with st.status("Analyse wird durchgefuehrt...", expanded=True) as status:
                assistant_msg = _run_normal_mode(user_input, selected_model)
                status.update(label="Analyse abgeschlossen", state="complete", expanded=False)

    # In History speichern
    st.session_state["chat_messages"].append(assistant_msg)

    # Turn-Limit durchsetzen
    st.session_state["chat_messages"] = enforce_turn_limit(st.session_state["chat_messages"])

    # Ergebnis persistieren
    _persist_query_result(user_input, assistant_msg)

    # Session-Metriken aktualisieren
    _update_session_metrics(assistant_msg.metrics)

    # Rerun fuer konsistentes Rendering aus History
    st.rerun()
