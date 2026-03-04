"""Dashboard fuer historische Modellvergleiche.

Liest gespeicherte Vergleichsergebnisse aus results/comparisons/ und
zeigt sie als Tabellen und Balkendiagramme fuer die Seite-an-Seite-Bewertung.
Erweitert um Per-Call-Breakdown, statistische Ergebnisse, Code-Vergleich
und CSV-Download fuer Thesis-Plots.
"""
from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd
import streamlit as st

from modules.logger import get_logger

logger = get_logger("debug")

COMPARISON_DIR = Path("results") / "comparisons"


def _load_comparisons() -> list[dict]:
    """Laedt alle Vergleichs-JSONs, neueste zuerst."""
    if not COMPARISON_DIR.exists():
        return []
    files = sorted(COMPARISON_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    comparisons = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                comparisons.append(json.load(fh))
        except Exception as exc:
            logger.warning("Vergleich %s konnte nicht geladen werden: %s", f, exc)
    return comparisons


def _build_aggregate_csv(comparisons: list[dict]) -> str:
    """Erstellt eine aggregierte CSV ueber alle Vergleiche fuer Thesis-Plots."""
    buf = io.StringIO()
    fieldnames = [
        "timestamp", "question", "model", "success", "analysis_type",
        "decision_type", "prompt_tokens", "completion_tokens",
        "reasoning_tokens", "total_tokens", "cost_usd", "duration_seconds",
        "moran_I", "p_value", "n",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for comp in comparisons:
        ts = comp.get("timestamp", "")
        question = comp.get("question", "")
        for row in comp.get("results", []):
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
                "timestamp": ts,
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
    return buf.getvalue()


def _render_per_call_breakdown(results: list[dict]) -> None:
    """Rendert die Per-LLM-Call-Aufschluesselung pro Modell."""
    has_per_call = any(row.get("per_call_metrics") for row in results)
    if not has_per_call:
        return

    st.markdown("**Aufschluesselung nach LLM-Aufruf:**")
    rows = []
    for result in results:
        model = result.get("model", "?")
        for call in result.get("per_call_metrics", []):
            rows.append({
                "Modell": model,
                "Funktion": call.get("function", "?"),
                "Prompt": call.get("prompt_tokens", 0),
                "Completion": call.get("completion_tokens", 0),
                "Reasoning": call.get("reasoning_tokens", 0),
                "Tokens": call.get("total_tokens", 0),
                "Kosten ($)": f"{call.get('cost_usd', 0):.4f}",
                "Dauer (s)": f"{call.get('duration_seconds', 0):.1f}",
            })
    if rows:
        st.table(rows)


def _render_statistical_results(results: list[dict]) -> None:
    """Rendert statistische Ergebnisse (Moran's I, p-Wert) pro Modell."""
    has_stats = any(row.get("summary_json") for row in results)
    if not has_stats:
        return

    st.markdown("**Statistische Ergebnisse:**")
    rows = []
    for result in results:
        model = result.get("model", "?")
        sj = result.get("summary_json") or {}
        # Top-level Ergebnisse
        if "moran_I" in sj:
            rows.append({
                "Modell": model,
                "Gruppe": "-",
                "Moran's I": f"{sj['moran_I']:.6f}" if sj["moran_I"] is not None else "NaN",
                "p-Wert": f"{sj.get('p_value', 'NaN'):.6f}" if sj.get("p_value") is not None else "NaN",
                "n": sj.get("n", "?"),
            })
        else:
            # Verschachtelte Gruppen-Ergebnisse
            for group, vals in sj.items():
                if isinstance(vals, dict) and "moran_I" in vals:
                    rows.append({
                        "Modell": model,
                        "Gruppe": group,
                        "Moran's I": f"{vals['moran_I']:.6f}" if vals["moran_I"] is not None else "NaN",
                        "p-Wert": f"{vals.get('p_value', 'NaN'):.6f}" if vals.get("p_value") is not None else "NaN",
                        "n": vals.get("n", "?"),
                    })
    if rows:
        st.table(rows)


def _render_code_comparison(results: list[dict]) -> None:
    """Rendert generierten Code nebeneinander fuer Modellvergleich."""
    code_key = "python_code" if any(r.get("python_code") for r in results) else "cypher_query"
    has_code = any(r.get(code_key) for r in results)
    if not has_code:
        return

    lang = "python" if code_key == "python_code" else "cypher"
    label = "Python-Code" if code_key == "python_code" else "Cypher-Query"

    with st.expander(f"Generierter {label} im Vergleich"):
        cols = st.columns(len(results))
        for col, row in zip(cols, results):
            with col:
                st.markdown(f"**{row.get('model', '?')}**")
                code = row.get(code_key, "Kein Code")
                st.code(code[:5000], language=lang)


def _render_aggregate_stats(comparisons: list[dict]) -> None:
    """Rendert aggregierte Statistiken ueber alle Vergleiche."""
    model_stats: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "successes": 0,
        "total_tokens": 0, "cost_usd": 0.0, "duration_seconds": 0.0,
    })

    for comp in comparisons:
        for row in comp.get("results", []):
            model = row.get("model", "?")
            m = row.get("metrics", {})
            stats = model_stats[model]
            stats["count"] += 1
            stats["successes"] += 1 if row.get("success") else 0
            stats["total_tokens"] += m.get("total_tokens", 0)
            stats["cost_usd"] += m.get("cost_usd", 0)
            stats["duration_seconds"] += m.get("duration_seconds", 0)

    if not model_stats:
        return

    st.subheader("Aggregierte Statistik")
    rows = []
    for model, stats in sorted(model_stats.items()):
        n = stats["count"]
        rows.append({
            "Modell": model,
            "Vergleiche": n,
            "Erfolgsrate": f"{stats['successes'] / n * 100:.0f}%" if n else "-",
            "Ø Tokens": f"{stats['total_tokens'] / n:.0f}" if n else "-",
            "Ø Kosten ($)": f"{stats['cost_usd'] / n:.4f}" if n else "-",
            "Ø Dauer (s)": f"{stats['duration_seconds'] / n:.1f}" if n else "-",
            "Σ Kosten ($)": f"{stats['cost_usd']:.4f}",
        })
    st.table(rows)


def show_comparison_dashboard() -> None:
    """Rendert die Modellvergleich-Dashboard-Seite."""
    st.header("Modellvergleich Dashboard")

    comparisons = _load_comparisons()

    if not comparisons:
        st.info("Noch keine Vergleiche vorhanden. Nutze den Vergleichsmodus im Chat.")
        return

    st.markdown(f"**{len(comparisons)}** gespeicherte Vergleiche")

    # Aggregierte Statistik und CSV-Download oben
    _render_aggregate_stats(comparisons)

    csv_data = _build_aggregate_csv(comparisons)
    st.download_button(
        label="CSV-Export (alle Vergleiche)",
        data=csv_data,
        file_name="modellvergleiche.csv",
        mime="text/csv",
    )

    st.divider()

    # Einzelvergleiche
    for i, comp in enumerate(comparisons):
        question = comp.get("question", "?")
        ts = comp.get("timestamp", "?")
        results = comp.get("results", [])

        with st.expander(f"{ts} -- {question[:80]}", expanded=(i == 0)):
            if not results:
                st.warning("Keine Ergebnisse in diesem Vergleich.")
                continue

            # Zusammenfassungstabelle
            table_data = []
            models = []
            tokens_list = []
            costs_list = []
            durations_list = []

            for row in results:
                m = row.get("metrics", {})
                model = row.get("model", "?")
                models.append(model)
                tokens_list.append(m.get("total_tokens", 0))
                costs_list.append(m.get("cost_usd", 0))
                durations_list.append(m.get("duration_seconds", 0))

                table_data.append({
                    "Modell": model,
                    "Erfolg": "Ja" if row.get("success") else "Nein",
                    "Typ": row.get("analysis_type", "?"),
                    "Tokens": m.get("total_tokens", 0),
                    "Prompt": m.get("prompt_tokens", 0),
                    "Completion": m.get("completion_tokens", 0),
                    "Reasoning": m.get("reasoning_tokens", 0),
                    "Kosten ($)": f"{m.get('cost_usd', 0):.4f}",
                    "Dauer (s)": f"{m.get('duration_seconds', 0):.1f}",
                })

            st.table(table_data)

            # Balkendiagramme nebeneinander
            if len(models) >= 2:
                col1, col2, col3 = st.columns(3)

                with col1:
                    st.markdown("**Tokens**")
                    st.bar_chart(dict(zip(models, tokens_list)))

                with col2:
                    st.markdown("**Kosten ($)**")
                    st.bar_chart(dict(zip(models, costs_list)))

                with col3:
                    st.markdown("**Dauer (s)**")
                    st.bar_chart(dict(zip(models, durations_list)))

            # Per-Call-Breakdown
            _render_per_call_breakdown(results)

            # Statistische Ergebnisse
            _render_statistical_results(results)

            # Antworten nebeneinander
            if len(results) >= 2:
                st.markdown("**Antworten im Vergleich:**")
                cols = st.columns(len(results))
                for col, row in zip(cols, results):
                    with col:
                        st.markdown(f"**{row.get('model', '?')}**")
                        explanation = row.get("explanation", "Keine Antwort")
                        st.markdown(explanation[:1000])

            # Code-Vergleich
            _render_code_comparison(results)
