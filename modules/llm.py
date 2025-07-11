import json
from typing import Optional
import os
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
analysis_patterns = set(SUPPORTED_ANALYSES)

def _validate_metrics(metrics: list[str]) -> None:
    allowed = {f"site_{k}" for k in concepts["site_keys"]} | \
              {f"feature_{k}" for k in concepts["feature_keys"]}
    for m in metrics:
        if m not in allowed:
            raise ValueError(
                f"Invalid metric '{m}'. Allowed metrics are prefixed columns: {sorted(list(allowed))[:10]} …"
            )


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




def generate_analysis_code(user_input: str, structure: dict, analysis_type: str, model: Optional[str] = None) -> list[dict]:
    from modules.helper import call_llm_with_prompt, strip_code_fences, render_template
    from modules.llm import concepts

    outputs = []

    # 1. Parameter extrahieren
    param_prompt = render_template(f"{analysis_type}_params.jinja2", {
        "question": user_input,
        "concepts": concepts,
        "structure": structure
    }, folder="params")

    raw = call_llm_with_prompt(f"{analysis_type}_params", user_input, param_prompt, "", model=model)

    try:
        parameters = json.loads(strip_code_fences(raw))
    except Exception:
        parameters = {}
        print(f"⚠️ Parameter-Parsing fehlgeschlagen für {analysis_type}")

    # 2. Analysecode erzeugen
    try:
        code_block = render_template(f"{analysis_type}_code.jinja2", parameters, folder="codes")
    except Exception as e:
        code_block = f"Fehler beim Rendern des Codes für {analysis_type}: {e}"

    # 3. Ergebnis zurückgeben
    outputs.append({
        "analysis_type": analysis_type,
        "parameters": parameters,
        "code": code_block
    })

    return outputs



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


def extract_relevant_data(question: str, structure: dict | None = None, path: str = "results/analysis_input.json") -> list[dict]:
    if structure is None:
        structure = extract_semantic_structure(question)
    filters = []
    return_fields = set([
        "f.FeatureID AS FeatureID",
        "f.Category AS feature_Category",
        "f.Location1 AS feature_Location1",
        "f.X AS feature_X",
        "f.Y AS feature_Y",
        "s.SiteID AS SiteID",
        "s.Category AS site_Category",
        "s.Location1 AS site_Location1",
        "s.X AS site_X",
        "s.Y AS site_Y"
    ])
    feature_categories = []

    for node in structure.get("nodes", []):
        if node.get("type") == "Feature":
            feature_categories += node.get("categories", [])

        for key, value in node.get("filters", {}).items():
            filter_expr, alias = _resolve_filter_field(key, value, node.get("type", ""))
            filters.append(filter_expr)
            return_fields.add(alias)

    if not feature_categories:
        raise ValueError("Keine gültigen Feature-Kategorien erkannt.")

    filters.insert(0, f"f.Category IN [{', '.join(repr(c) for c in feature_categories)}]")
    _validate_metrics(structure.get("metrics", []))

    return_fields |= _extract_metric_fields(structure.get("metrics", []))

    cypher = (
        f"MATCH (s:Site)-[:HAS_FEATURE]->(f:Feature) "
        f"WHERE {' AND '.join(filters)} "
        f"RETURN {', '.join(sorted(return_fields))}"
    )

    rows = run_cypher(cypher)
    if rows:
        preview = rows[0]
        log_json(logger, "debug", "🧪 Vorschau erster Row (Neo4j)", preview)
        log_json(logger, "debug", "🧩 Felder", list(preview.keys()))
    else:
        logger.warning("⚠️ Cypher gab keine Zeilen zurück")
    if not rows:
        raise ValueError("Export returned no data.")

    # --- NaN zu Leerstring nur für Strings ---
    df = pd.DataFrame(rows)
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].fillna("")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_json(path, orient="records", indent=2, force_ascii=False)

    return df.to_dict(orient="records")



def _resolve_filter_field(key: str, value: str, node_type: str) -> tuple[str, str]:
    key_l = key.lower()
    skeys = {k.lower(): k for k in concepts["site_keys"]}
    fkeys = {k.lower(): k for k in concepts["feature_keys"]}

    # explizite Prefixe
    if key.startswith("site_"):
        raw = key[5:].lower()
        if raw not in skeys:
            raise ValueError(f"Ungültiger site_-Filter: {key}")
        col = f"s.{skeys[raw]}"
        return f"{col} = {repr(value)}", f"{col} AS site_{skeys[raw]}"

    if key.startswith("feature_"):
        raw = key[8:].lower()
        if raw not in fkeys:
            raise ValueError(f"Ungültiger feature_-Filter: {key}")
        col = f"f.{fkeys[raw]}"
        return f"{col} = {repr(value)}", f"{col} AS feature_{fkeys[raw]}"

    # unpräfixte Schlüssel – Ambiguität behandeln
    if key_l in skeys and key_l not in fkeys:
        col = f"s.{skeys[key_l]}"
        return f"{col} = {repr(value)}", f"{col} AS site_{skeys[key_l]}"
    elif key_l in fkeys and key_l not in skeys:
        col = f"f.{fkeys[key_l]}"
        return f"{col} = {repr(value)}", f"{col} AS feature_{fkeys[key_l]}"
    elif key_l in skeys and key_l in fkeys:
        # → Ambiguität auflösen über Typ
        if node_type == "Feature":
            col = f"f.{fkeys[key_l]}"
            return f"{col} = {repr(value)}", f"{col} AS feature_{fkeys[key_l]}"
        elif node_type == "Site":
            col = f"s.{skeys[key_l]}"
            return f"{col} = {repr(value)}", f"{col} AS site_{skeys[key_l]}"
        else:
            raise ValueError(f"Ambiguöser Filter ohne Typauflösung: {key}")
    else:
        raise ValueError(f"Unbekannter Filter: {key}")


def _extract_metric_fields(metrics: list[str]) -> set[str]:
    fields = set()
    skeys = {k.lower(): k for k in concepts["site_keys"]}
    fkeys = {k.lower(): k for k in concepts["feature_keys"]}

    for metric in metrics:
        m = metric.strip()
        m_l = m.lower()

        # explizit prefix-basiert
        if m.startswith("site_"):
            raw = m[5:].lower()
            if raw not in skeys:
                raise ValueError(f"Ungültige site_-Metrik: {m}")
            col = skeys[raw]
            fields.add(f"s.{col} AS site_{col}")

        elif m.startswith("feature_"):
            raw = m[8:].lower()
            if raw not in fkeys:
                raise ValueError(f"Ungültige feature_-Metrik: {m}")
            col = fkeys[raw]
            fields.add(f"f.{col} AS feature_{col}")

        else:
            raise ValueError(f"Metriken müssen `site_` oder `feature_` prefix haben: {m}")

    return fields



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
