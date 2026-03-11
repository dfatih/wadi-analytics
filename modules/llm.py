"""LLM-Agent fuer archaeologische Analysen via OpenAI Tool-Use.

Stellt einen einzelnen Einstiegspunkt run_agent() bereit, der eine Nutzerfrage
im agentischen Loop beantwortet. Das LLM entscheidet autonom, welche Tools
(Cypher-Abfragen, Python-Analysen) aufgerufen werden.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Optional

from modules.helper import (
    AgentResult,
    ToolCallRecord,
    call_llm_with_tools,
    load_yaml,
    render_template,
    run_cypher,
    run_python_code,
    strip_code_fences,
)
from modules.language import detect_language, language_name
from modules.disambiguator import (
    resolve_terms,
    format_resolved_terms,
    validate_cypher_values,
    auto_correct_cypher,
    fix_cypher_syntax,
)
from modules.logger import get_logger

logger = get_logger("debug")

concepts = load_yaml("concepts.yml")


# ---------------------------------------------------------------------------
# Tool-Schemata fuer OpenAI Function Calling
# ---------------------------------------------------------------------------
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "run_cypher_query",
            "description": (
                "Execute a Cypher query against the Neo4j graph database. "
                "Returns query results as JSON. Data is automatically saved to a file "
                "for use in subsequent spatial analysis. If the query fails, "
                "the error message is returned so you can fix and retry."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A valid Cypher query starting with MATCH",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_spatial_analysis",
            "description": (
                "Execute a Python spatial analysis script. The script should read "
                "data from the JSON file saved by a previous run_cypher_query call, "
                "perform the spatial analysis, and save results to "
                "results/visualisierung/{analysis_type}/. "
                "Returns stdout, stderr, and summary statistics."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Complete Python script to execute",
                    },
                    "analysis_type": {
                        "type": "string",
                        "description": "Type of spatial analysis",
                        "enum": [
                            "autocorrelation", "colocation", "correlation",
                            "ripley_k", "hotspot", "spatial_distance",
                        ],
                    },
                },
                "required": ["code", "analysis_type"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Cypher-Bereinigung
# ---------------------------------------------------------------------------
def _sanitize_cypher(cypher: str) -> str:
    """Bereinigt LLM-generiertes Cypher von gaengigen Artefakten."""
    cypher = re.sub(r'\\\s*\n', ' ', cypher)
    cypher = re.sub(r'\n{3,}', '\n\n', cypher)
    return cypher.strip()


# ---------------------------------------------------------------------------
# Tool-Handler
# ---------------------------------------------------------------------------
def _handle_tool_call(
    tool_name: str, arguments: dict, context: dict,
) -> tuple[str, ToolCallRecord]:
    """Verarbeitet einen Tool-Aufruf und gibt (result_text, record) zurueck."""
    if tool_name == "run_cypher_query":
        return _handle_cypher_query(arguments, context)
    elif tool_name == "run_spatial_analysis":
        return _handle_spatial_analysis(arguments, context)
    else:
        msg = f"Unbekanntes Tool: {tool_name}"
        record = ToolCallRecord(
            tool_name=tool_name, arguments=arguments,
            result_text=msg, success=False,
        )
        return msg, record


def _handle_cypher_query(
    arguments: dict, context: dict,
) -> tuple[str, ToolCallRecord]:
    """Fuehrt eine Cypher-Abfrage aus: validiert, korrigiert, fuehrt aus, speichert."""
    query = arguments.get("query", "")
    data_path = context.get("data_path", "results/analysis_input.json")
    record = ToolCallRecord(
        tool_name="run_cypher_query",
        arguments=arguments,
        result_text="",
        cypher_query=query,
    )

    try:
        # Bereinigen
        query = _sanitize_cypher(query)
        query, syntax_fixes = fix_cypher_syntax(query)
        if syntax_fixes:
            logger.info("Cypher-Syntax korrigiert: %s", syntax_fixes)

        # Werte validieren und auto-korrigieren
        warnings = validate_cypher_values(query)
        if warnings:
            logger.warning("Cypher-Validierungswarnungen: %s", warnings)
            query, corrections = auto_correct_cypher(query)
            if corrections:
                logger.info("Automatisch korrigiert: %s", corrections)

        record.cypher_query = query

        # Ausfuehren
        rows = run_cypher(query)
        logger.info("Cypher-Abfrage: %d Zeilen zurueckgegeben", len(rows))

        # Daten speichern
        parent_dir = os.path.dirname(data_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(data_path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False)

        # Antwort fuer LLM zusammenbauen
        preview = json.dumps(rows[:10], indent=2, ensure_ascii=False)
        columns = list(rows[0].keys()) if rows else []
        result_text = (
            f"Query returned {len(rows)} rows. "
            f"Data saved to: {data_path}\n"
            f"Columns: {columns}\n\n"
            f"Preview (first 10 rows):\n{preview}"
        )

        record.result_text = result_text
        record.stdout = preview
        record.success = True
        return result_text, record

    except Exception as exc:
        error_msg = f"Cypher-Fehler: {exc}"
        logger.error("Cypher-Ausfuehrung fehlgeschlagen: %s", exc)
        record.result_text = error_msg
        record.stderr = str(exc)
        record.success = False
        return error_msg, record


def _handle_spatial_analysis(
    arguments: dict, context: dict,
) -> tuple[str, ToolCallRecord]:
    """Fuehrt ein Python-Analyse-Skript aus und sammelt Ergebnisse."""
    code = arguments.get("code", "")
    analysis_type = arguments.get("analysis_type", "unknown")
    started_at = context.get("step_started", 0.0)

    record = ToolCallRecord(
        tool_name="run_spatial_analysis",
        arguments={"analysis_type": analysis_type},
        result_text="",
        python_code=code,
    )

    try:
        stdout, stderr = run_python_code(code)
    except Exception as exc:
        error_msg = f"Python-Ausfuehrung fehlgeschlagen: {exc}"
        logger.error("Python-Ausfuehrung fehlgeschlagen: %s", exc)
        record.result_text = error_msg
        record.stderr = str(exc)
        record.success = False
        return error_msg, record

    record.stdout = stdout
    record.stderr = stderr

    # Fehler-Erkennung
    has_error = stderr and any(
        kw in stderr for kw in ["Traceback", "Error", "Exception"]
    )

    # GeoJSON suchen
    geojson_dir = Path(f"results/visualisierung/{analysis_type}")
    geojson_files = list(geojson_dir.glob("*.geojson")) if geojson_dir.exists() else []
    if geojson_files:
        latest = max(geojson_files, key=lambda f: f.stat().st_mtime)
        record.geojson_path = str(latest)

    # Summary-JSON laden
    summary = _load_summary_json(analysis_type, written_after=started_at)
    record.summary_json = summary

    # Statistische Validierung
    is_valid, val_warnings = _validate_summary_json(summary)
    if not is_valid:
        logger.warning("Statistische Validierung fehlgeschlagen: %s", val_warnings)
        has_error = True

    record.success = not has_error

    # Antwort fuer LLM zusammenbauen
    parts = []
    if stdout and stdout.strip():
        parts.append(f"stdout:\n{stdout.strip()}")
    if has_error and stderr:
        parts.append(f"stderr:\n{stderr.strip()}")
    if val_warnings:
        parts.append(f"Validierungswarnungen: {'; '.join(val_warnings)}")
    if summary:
        parts.append(f"Summary JSON: {json.dumps(summary, indent=2, ensure_ascii=False)}")

    result_text = "\n\n".join(parts) if parts else "Analyse abgeschlossen (keine Ausgabe)."
    record.result_text = result_text
    return result_text, record


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def _load_summary_json(analysis_type: str, written_after: float = 0.0) -> dict | None:
    """Laedt das Summary-JSON eines Python-Analyseschritts."""
    result_dir = Path("results") / "visualisierung" / analysis_type
    if not result_dir.exists():
        return None
    json_files = [
        f for f in result_dir.glob("*.json")
        if f.stat().st_mtime > written_after
    ]
    if not json_files:
        return None
    latest = max(json_files, key=lambda f: f.stat().st_mtime)
    try:
        with open(latest, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _validate_summary_json(summary: dict | None) -> tuple[bool, list[str]]:
    """Validiert Summary-JSON auf NaN-Werte in Moran's I und p-Werten."""
    if summary is None:
        return True, []
    warnings: list[str] = []

    def _check(d: dict, prefix: str = "") -> None:
        for key, val in d.items():
            if isinstance(val, dict):
                _check(val, prefix=f"[{key}] ")
                continue
            k = key.lower()
            if k in ("moran_i", "i") and isinstance(val, float) and math.isnan(val):
                warnings.append(f"{prefix}Moran's I ist NaN -- konstante Variable")
            if k in ("p_value", "p_sim") and isinstance(val, float) and math.isnan(val):
                warnings.append(f"{prefix}p-Wert ist NaN")

    _check(summary)
    return len(warnings) == 0, warnings


# ---------------------------------------------------------------------------
# Haupteinstiegspunkt
# ---------------------------------------------------------------------------
def run_agent(
    question: str,
    model: Optional[str] = None,
    data_path: str = "results/analysis_input.json",
    cell_size: int = 2000,
    client=None,
    temperature: Optional[float] = None,
) -> AgentResult:
    """Beantwortet eine archaeologische Forschungsfrage im agentischen Loop.

    Args:
        question: Die Nutzerfrage.
        model: Optionaler Modellname (Default aus Registry).
        data_path: Pfad fuer zwischengespeicherte Cypher-Ergebnisse.
        cell_size: Grid-Zellgroesse in Metern fuer raeumliche Analysen.
        client: Optionaler OpenAI/AzureOpenAI-Client.
        temperature: Optionale Temperatur-Ueberschreibung.

    Returns:
        AgentResult mit Antworttext, Tool-Aufrufen und Metriken.
    """
    import time

    # Begriffe vorab aufloesen (deterministisch, kein LLM-Aufruf)
    resolved = resolve_terms(question)
    resolved_text = format_resolved_terms(resolved)

    # Sprache der Benutzerfrage erkennen
    user_lang = detect_language(question)
    user_lang_name = language_name(user_lang)

    # System-Prompt aus Template rendern
    system_prompt = render_template("agent_system.jinja2", {
        "concepts": concepts,
        "resolved_terms": resolved_text,
        "cell_size": cell_size,
        "user_lang": user_lang,
        "user_lang_name": user_lang_name,
    }, folder="system")

    # User-Nachricht mit aufgeloesten Begriffen anreichern
    user_message = question
    if resolved_text:
        user_message = f"{question}\n\n{resolved_text}"

    # Kontext fuer Tool-Handler
    context = {
        "data_path": data_path,
        "step_started": time.time(),
    }

    def tool_handler(name: str, args: dict) -> tuple[str, ToolCallRecord]:
        context["step_started"] = time.time()
        return _handle_tool_call(name, args, context)

    # Agenten-Loop ausfuehren
    result = call_llm_with_tools(
        system_prompt=system_prompt,
        user_message=user_message,
        tools=TOOL_SCHEMAS,
        tool_handler=tool_handler,
        model=model,
        max_iterations=10,
        client=client,
        temperature=temperature,
    )

    return result
