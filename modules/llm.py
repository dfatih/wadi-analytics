import json
from typing import Optional
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
    if stderr.strip():
        return f"Die Analyse konnte nicht durchgeführt werden."
    if not stdout.strip():
        return "Die Analyse lieferte keine Ausgaben."
    if "error" in stdout.lower():
        return "Die Analyse konnte nicht durchgeführt werden."

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




def generate_analysis_code(
    user_input: str,
    structure: dict,
    analysis_type: str,
    model: Optional[str] = None
) -> List[Dict]:
    """Return a parameter JSON + executable Python code block for the requested analysis."""
    # 1 ─ Parameter extraction ───────────────────────────────────────────
    param_prompt = render_template(
        "analysis_params.jinja2",
        {
            "question":       user_input,
            "concepts":       concepts,
            "structure":      structure,
            "analysis_type":  analysis_type,
        },
        folder="system",
    )
    raw = call_llm_with_prompt(
        function_name="analysis_params",
        question=user_input,
        prompt=param_prompt,
        preview=json.dumps(structure, indent=2),
        model=model,
    )

    try:
        params = json.loads(strip_code_fences(raw))
    except Exception as exc:
        logger.warning("Parameter parsing failed (%s) → fallback to {}", exc, analysis_type)
        params = {}

    # Ensure every required key exists (None if absent)
    req_keys = {
        "autocorrelation": ["x_column","y_column","value_column",
                            "group_column","group_a","group_b","distance_threshold"],
        "colocation":      ["x_column","y_column",
                            "group_a","group_b","group_a_type","group_b_type",
                            "filter_a_column","filter_a_value",
                            "filter_b_column","filter_b_value",
                            "distance_threshold"],
        # … other types omitted for brevity
    }[analysis_type]
    for k in req_keys:
        params.setdefault(k, None)

    # 2 ─ Code generation ────────────────────────────────────────────────
    code_block = render_template(
        "analysis_code.jinja2",
        {
            "analysis_type": analysis_type,
            "params":        params,
            "concepts":      concepts,
        },
        folder="system",
    )

    return [{
        "analysis_type": analysis_type,
        "parameters": params,
        "code": code_block,
    }]




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
