from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import os
from typing import Optional, Any, List, Dict
import pandas as pd
from modules.helper import (
    load_llm_json,
    load_prompt,
    call_llm_with_prompt,
    strip_code_fences,
    render_template,
    load_yaml,
    sanitize_cypher_code,
    run_cypher
)
from modules.logger import get_logger, log_json
logger = get_logger("debug")

concepts = load_yaml("concepts.yml")
SUPPORTED_ANALYSES = [
    "autocorrelation",
    "colocation",
    "correlation",
    "ripley_k",
    "hotspot",
    "spatial_distance",
]

ALLOWED_FEATURE_KEYS = set(concepts.get("feature_keys", []))
ALLOWED_SITE_KEYS    = set(concepts.get("site_keys", []))
analysis_patterns = set(SUPPORTED_ANALYSES)


def explain_de(question: str, stdout: str, stderr: str, *, model: Optional[str] = None) -> str:
    if any(keyword in stderr for keyword in ["Traceback", "Error", "Exception"]):
        return f"Die Analyse konnte nicht durchgeführt werden."
    if not stdout.strip():
        return "Die Analyse lieferte keine Ausgaben."

    prompt = render_template("explain_de.jinja2", {
        "question": question,
        "preview": stdout.strip()
    }, folder="system")
    return call_llm_with_prompt(
        function_name="explain_de",
        question=question,
        prompt=prompt,
        preview=stdout.strip(),
        model=model
    )


def explain_cypher_result(question: str, rows: list[dict], *, model: Optional[str] = None) -> str:
    preview = json.dumps(rows[:5], indent=2, ensure_ascii=False)

    prompt = render_template("explain_cypher_result.jinja2", {
        "question": question,
        "concepts": concepts
    }, folder="system")

    return call_llm_with_prompt(
        function_name="explain_cypher_result",
        question=question,
        prompt=prompt,
        preview=preview,
        model=model
    )




# modules/generate_analysis_code.py



def generate_analysis_code(
    user_input: str,
    structure: Dict,          # == parsed analysis_input.json (first rows are enough)
    analysis_type: str,
    model: Optional[str] = None,
) -> List[Dict]:
    """
    Ask the LLM to write a *complete* Python script that performs the requested
    analysis.  No parameter/‑code separation; the single template frames the
    entire prompt.

    Returns a list with one dict so the caller’s downstream JSON logger remains
    unchanged.
    """

    # 1 ─ Render the single prompt ───────────────────────────────────────
    prompt = render_template(
        "generate_analysis_code.jinja2",
        {
            "question":      user_input,
            "analysis_type": analysis_type,
            "concepts":      concepts,                # generic background
            "preview_json":  json.dumps(structure, indent=2)[:1_000],  # guard length
        },
        folder="system",
    )

    # 2 ─ Call LLM ───────────────────────────────────────────────────────
    raw_answer = call_llm_with_prompt(
        function_name="generate_analysis_code",
        question=user_input,
        prompt=prompt,
        preview=json.dumps(structure, indent=2)[:1_000],
        model=model,
    )

    # 3 ─ Extract code block only ────────────────────────────────────────
    try:
        code_block = strip_code_fences(raw_answer).strip()
    except Exception as exc:
        logger.error("⚠️ Could not strip code fences: %s", exc)
        code_block = raw_answer

    # 4 ─ Provenance record ──────────────────────────────────────────────
    record = {
        "timestamp":     datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "question":      user_input,
        "analysis_type": analysis_type,
        "llm_prompt":    prompt,
        "code":          code_block,
        "preview":       json.dumps(structure, indent=2).splitlines()[:5],
    }
    return [record]





def generate_cypher(question: str, *, model: Optional[str] = None) -> str:
    """
    Erzeugt einen Cypher-Query durch das LLM basierend auf einem systemweiten Template.
    Verwendet das Template: templates/generate_cypher.jinja2
    """

    # 1. Systemprompt aus Template generieren
    prompt = render_template("generate_cypher.jinja2", {
        "question": question,
        "concepts": concepts
    }, folder="system")

    # 2. LLM aufrufen
    raw_code = call_llm_with_prompt(
        function_name="generate_cypher",
        question=question,
        prompt=prompt,
        preview="",
        model=model,
    )

    # 3. Code bereinigen (z. B. ```cypher entfernen)
    return sanitize_cypher_code(raw_code)



    
def extract_semantic_structure(question: str, analysis_type: Optional[str] = None, model: Optional[str] = None) -> dict:
    prompt = render_template("extract_semantic_structure.jinja2", {
        "question": question,
        "concepts": concepts,
        "analysis_type": analysis_type or "",  # leer als fallback
    }, folder="system")

    raw = call_llm_with_prompt("extract_semantic_structure", question, prompt, "", model=model)

    try:
        result = load_llm_json(raw)
        if not isinstance(result, dict):
            return {"analysis_type": []}

        if "analysis_types" not in result and "analysis_type" in result:
            result["analysis_types"] = (
                [result["analysis_type"]] if isinstance(result["analysis_type"], str) else result["analysis_type"]
            )
        return result
    except Exception:
        return {"analysis_type": []}


def decide_query_or_python(user_input: str) -> tuple[str, dict, str]:
    # Schritt 1: Typ klassifizieren
        # Schritt 1: Typ klassifizieren
    prompt = render_template("classify_analysis_type.jinja2", {
        "question": user_input
    }, folder="system")

    try:
        raw = call_llm_with_prompt("classify_analysis_type", user_input, prompt, "")
        analysis_types = json.loads(strip_code_fences(raw))["analysis_types"]
        analysis_types = [a.strip().lower() for a in analysis_types]
        logger.info(f"🧠 Analyse-Typen erkannt: {analysis_types}")
    except Exception as e:
        logger.error(f"❌ Fehler bei der Typ-Klassifizierung: {e}")
        return [("cypher", {}, "")]

    results = []
    for analysis_type in analysis_types:
        try:
            structure = extract_semantic_structure(user_input, analysis_type=analysis_type)
            decision = "python" if analysis_type in analysis_patterns else "cypher"
            results.append((decision, structure, analysis_type))
            logger.debug(f"📦 Struktur für {analysis_type.upper()}:\n{json.dumps(structure, indent=2)}")
        except Exception as e:
            logger.error(f"❌ Fehler bei Extraktion für Typ '{analysis_type}': {e}")
            continue

    return results




def extract_relevant_data(
    question: str,
    structure: dict | None = None,
    path: str = "results/analysis_input.json",
    model: Optional[str] = None,
) -> List[Dict]:
    """Get a complete Cypher query from the LLM (JSON key `cypher`),
    execute it, persist the rows, and return them."""
    # 1 ─ Render prompt
    prompt = render_template(
        "extract_relevant_headers.jinja2",
        {"question": question, "concepts": concepts, "structure": structure or {}},
        folder="system",
    )

    # 2 ─ Call LLM
    raw = call_llm_with_prompt("extract_relevant_headers", question, prompt, "", model=model)

    # 3 ─ Parse JSON  (strip ``` fences if present)
    try:
        clauses = load_llm_json(raw)                     # helper already strips fences
        cypher  = clauses.get("cypher")
        if not cypher:
            raise ValueError("key `cypher` missing or empty")
    except Exception as exc:
        logger.error("❌ LLM did not return valid JSON with a `cypher` key: %s", exc)
        raise

    # 4 ─ Sanity check
    if not cypher.lstrip().lower().startswith("match"):
        raise ValueError("Cypher string does not start with MATCH:\n" + cypher[:120])

    # 5 ─ Execute
    try:
        rows = run_cypher(cypher)
        logger.info("Retrieved %d rows via extract_relevant_data", len(rows))
    except Exception as exc:
        logger.exception("Cypher execution failed: %s", exc)
        raise

    # 6 ─ Persist
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)

    return rows
