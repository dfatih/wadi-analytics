"""Zentrale Hilfsfunktionen: LLM-Aufrufe, Template-Rendering, Cypher-Ausfuehrung.

Alle LLM-Aufrufe gehen durch call_llm_with_prompt() (Einzelaufrufe) oder
call_llm_with_tools() (Agenten-Loop mit Tool-Use). Beide geben strukturierte
Ergebnisse mit Metadaten (Tokens, Kosten, Dauer) zurueck.
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

import yaml
from jinja2 import Environment, FileSystemLoader
from neo4j import GraphDatabase
from openai import OpenAI

from modules.logger import get_logger, log_result

logger = get_logger("debug")

CLIENT = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ---------------------------------------------------------------------------
# LLM-Ergebnis -- wird von call_llm_with_prompt zurueckgegeben
# ---------------------------------------------------------------------------
@dataclass
class LLMResult:
    """Kapselt eine LLM-Antwort mit Metadaten (Tokens, Kosten, Dauer)."""
    answer: str
    metadata: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return self.answer

    def strip(self) -> str:
        return self.answer.strip()


# ---------------------------------------------------------------------------
# Sammelt alle LLM-Aufrufe pro Request-Zyklus (thread-safe)
# ---------------------------------------------------------------------------
_thread_local = threading.local()


def _get_llm_buffer() -> list[LLMResult]:
    """Gibt den thread-lokalen LLM-Ergebnispuffer zurueck."""
    if not hasattr(_thread_local, "llm_results"):
        _thread_local.llm_results = []
    return _thread_local.llm_results


def drain_llm_results() -> list[LLMResult]:
    """Gibt alle gesammelten LLM-Ergebnisse zurueck und leert den Puffer.

    Thread-safe: Jeder Thread hat seinen eigenen Puffer via threading.local().
    Funktioniert unveraendert fuer Streamlit (single-threaded pro Request).
    """
    buf = _get_llm_buffer()
    results = list(buf)
    buf.clear()
    return results


# ---------------------------------------------------------------------------
# Modell-Registry
# ---------------------------------------------------------------------------
def _load_model_registry() -> tuple[dict, str]:
    """Laedt die Model-Registry und den Default-Modellnamen aus config/models.yml."""
    cfg_path = Path(__file__).parent.parent / "config" / "models.yml"
    if not cfg_path.exists():
        logger.warning("models.yml nicht gefunden: %s", cfg_path)
        return {}, "gpt-4.1"
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    models = raw.get("models", {})
    default = raw.get("default_model", "gpt-4.1")
    return models, default


MODEL_REGISTRY, _YAML_DEFAULT = _load_model_registry()
DEFAULT_MODEL: str = os.getenv("OPENAI_MODEL", _YAML_DEFAULT)


def get_model_config(model_name: str) -> dict:
    """Gibt die Registry-Konfiguration fuer ein Modell zurueck (mit Fallback-Defaults)."""
    if model_name in MODEL_REGISTRY:
        return MODEL_REGISTRY[model_name]
    # Vernuenftige Defaults fuer unbekannte Modelle (Chat-Typ angenommen)
    return {
        "display_name": model_name,
        "api_name": model_name,
        "type": "chat",
        "supports_system_message": True,
        "supports_temperature": True,
        "default_temperature": 0.2,
        "cost_per_1k_prompt": 0.0,
        "cost_per_1k_completion": 0.0,
    }


def get_available_models() -> list[dict]:
    """Liefert die verfuegbaren Modelle fuer das Sidebar-Dropdown."""
    return [
        {"api_name": k, "display_name": v.get("display_name", k)}
        for k, v in MODEL_REGISTRY.items()
    ]


def _calculate_cost(
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    reasoning_tokens: int = 0,
) -> float:
    """Berechnet die USD-Kosten aus Token-Zaehlen anhand der Registry.

    Hinweis: OpenAIs completion_tokens enthaelt bereits die reasoning_tokens.
    Um Doppelzaehlung zu vermeiden, werden sichtbare Output-Tokens zum
    Completion-Tarif und Reasoning-Tokens zum (ggf. abweichenden)
    Reasoning-Tarif abgerechnet.
    """
    cfg = get_model_config(model_name)
    visible_completion = completion_tokens - reasoning_tokens
    cost = (prompt_tokens / 1000) * cfg.get("cost_per_1k_prompt", 0)
    cost += (visible_completion / 1000) * cfg.get("cost_per_1k_completion", 0)
    if reasoning_tokens:
        cost += (reasoning_tokens / 1000) * cfg.get("cost_per_1k_reasoning",
                                                      cfg.get("cost_per_1k_completion", 0))
    return round(cost, 6)


TEMPLATE_FOLDER = Path(__file__).parent.parent / "templates"
CONFIG_FOLDER   = Path(__file__).parent.parent / "config"

env = Environment(
    loader=FileSystemLoader(TEMPLATE_FOLDER),
    trim_blocks=True,
    lstrip_blocks=True
)


def render_template(name: str, context: dict, folder: str = "") -> str:
    """Rendert ein Jinja2-Template mit dem gegebenen Kontext."""
    if folder:
        path = TEMPLATE_FOLDER / folder / name
    else:
        path = TEMPLATE_FOLDER / name
    if not path.exists():
        raise FileNotFoundError(f"Template nicht gefunden: {path}")
    return env.get_template(path.relative_to(TEMPLATE_FOLDER).as_posix()).render(**context)

def strip_code_fences(txt: str) -> str:
    """Entfernt ```lang ... ``` Huellen (json, python, cypher, etc.)."""
    return re.sub(r"^```\w*\s*\n?|```\s*$", "", txt.strip(), flags=re.I | re.M).strip()

def load_yaml(name: str) -> dict:
    """Laedt eine YAML-Konfiguration aus dem config-Verzeichnis."""
    path = CONFIG_FOLDER / name
    if not path.exists():
        raise FileNotFoundError(f"YAML-Konfiguration nicht gefunden: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def call_llm_with_prompt(
    function_name: str,
    question: str,
    prompt: str,
    result_data=None,
    temperature: float = 0.2,
    model: Optional[str] = None,
) -> LLMResult:
    """Ruft die OpenAI-API auf mit modellspezifischer Parametrisierung.

    Gibt ein LLMResult zurueck, dessen str() den Antworttext liefert --
    bestehende Aufrufer, die den Rueckgabewert als String behandeln,
    funktionieren weiterhin.
    """
    effective_model = model or DEFAULT_MODEL
    cfg = get_model_config(effective_model)
    is_reasoning = cfg.get("type") == "reasoning"

    # Nachrichten zusammenbauen
    if is_reasoning:
        # Reasoning-Modelle: System-Prompt als Developer-Message
        messages = [
            {"role": "developer", "content": prompt},
            {"role": "user", "content": f"Frage: {question}"},
        ]
    else:
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Frage: {question}"},
        ]

    # API-Parameter
    kwargs: dict[str, Any] = {"model": effective_model, "messages": messages}
    if cfg.get("supports_temperature", True):
        kwargs["temperature"] = temperature

    # API-Aufruf
    start = time.time()
    response = CLIENT.chat.completions.create(**kwargs)
    duration = round(time.time() - start, 2)

    final_answer = response.choices[0].message.content.strip()

    # Token-Zaehler auslesen
    usage = response.usage
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    reasoning_tokens = 0
    if usage and hasattr(usage, "completion_tokens_details") and usage.completion_tokens_details:
        reasoning_tokens = getattr(usage.completion_tokens_details, "reasoning_tokens", 0) or 0

    cost = _calculate_cost(effective_model, prompt_tokens, completion_tokens, reasoning_tokens)

    metadata = {
        "function_name": function_name,
        "model": effective_model,
        "model_type": cfg.get("type", "chat"),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": cost,
        "duration_seconds": duration,
    }

    # Ergebnis loggen
    log_result(
        function_name=function_name,
        user_question=question,
        generated_prompt=prompt,
        result_data=result_data or [],
        llm_response=response.model_dump(),
        code_generated=final_answer,
        status="success",
        results_dir="results",
        model_used=effective_model,
        duration_seconds=duration,
        cost_usd=cost,
    )

    result = LLMResult(answer=final_answer, metadata=metadata)
    _get_llm_buffer().append(result)
    return result

# ---------------------------------------------------------------------------
# Agenten-Datenklassen
# ---------------------------------------------------------------------------
@dataclass
class ToolCallRecord:
    """Zeichnet einen einzelnen Tool-Aufruf des Agenten auf."""
    tool_name: str
    arguments: dict
    result_text: str
    success: bool = True
    cypher_query: str = ""
    python_code: str = ""
    stdout: str = ""
    stderr: str = ""
    geojson_path: str = ""
    summary_json: Optional[dict] = None


@dataclass
class AgentResult:
    """Vollstaendiges Ergebnis eines agentischen LLM-Laufs."""
    answer: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    model: str = ""


# ---------------------------------------------------------------------------
# Agenten-Loop (LLM mit Tool-Use)
# ---------------------------------------------------------------------------
def call_llm_with_tools(
    system_prompt: str,
    user_message: str,
    tools: list[dict],
    tool_handler: Callable[[str, dict], tuple[str, ToolCallRecord]],
    model: Optional[str] = None,
    max_iterations: int = 10,
    client: Optional[Any] = None,
    temperature: Optional[float] = None,
) -> AgentResult:
    """Fuehrt einen agentischen LLM-Loop mit Tool-Use durch.

    Das LLM entscheidet, welche Tools aufgerufen werden. tool_handler fuehrt
    sie aus und gibt (result_text_fuer_llm, record_fuer_ui) zurueck.
    Der Loop laeuft bis das LLM eine finale Textantwort gibt oder
    max_iterations erreicht ist.

    Args:
        client: Optionaler OpenAI/AzureOpenAI-Client. Default: globaler CLIENT.
        temperature: Optionale Temperatur-Ueberschreibung.
    """
    api_client = client or CLIENT
    effective_model = model or DEFAULT_MODEL
    cfg = get_model_config(effective_model)
    is_reasoning = cfg.get("type") == "reasoning"

    system_role = "developer" if is_reasoning else "system"
    messages: list[dict[str, Any]] = [
        {"role": system_role, "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    result = AgentResult(model=effective_model)
    start_time = time.time()

    for iteration in range(max_iterations):
        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "tools": tools,
        }
        effective_temp = temperature if temperature is not None else cfg.get("default_temperature", 0.2)
        if cfg.get("supports_temperature", True):
            kwargs["temperature"] = effective_temp

        response = api_client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        # Token-Zaehler akkumulieren
        usage = response.usage
        if usage:
            result.prompt_tokens += usage.prompt_tokens
            result.completion_tokens += usage.completion_tokens
            if hasattr(usage, "completion_tokens_details") and usage.completion_tokens_details:
                r = getattr(usage.completion_tokens_details, "reasoning_tokens", 0) or 0
                result.reasoning_tokens += r

        # LLM ist fertig (finale Textantwort)
        if choice.finish_reason == "stop":
            result.answer = choice.message.content or ""
            break

        # Tool-Aufrufe verarbeiten
        if choice.message.tool_calls:
            messages.append(choice.message)
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                result_text, record = tool_handler(tc.function.name, args)
                result.tool_calls.append(record)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })
        else:
            # Kein Tool-Aufruf und nicht "stop" -- als fertig behandeln
            result.answer = choice.message.content or ""
            break
    else:
        # Max-Iterationen erreicht
        if not result.answer:
            result.answer = "Maximale Iterationen erreicht."
            logger.warning("Agent-Loop: max_iterations (%d) erreicht", max_iterations)

    result.duration_seconds = round(time.time() - start_time, 2)
    result.total_tokens = result.prompt_tokens + result.completion_tokens
    result.cost_usd = _calculate_cost(
        effective_model, result.prompt_tokens,
        result.completion_tokens, result.reasoning_tokens,
    )

    # In LLM-Puffer aufnehmen (fuer Metriken-Tracking)
    _get_llm_buffer().append(LLMResult(
        answer=result.answer,
        metadata={
            "function_name": "run_agent",
            "model": effective_model,
            "model_type": cfg.get("type", "chat"),
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "reasoning_tokens": result.reasoning_tokens,
            "total_tokens": result.total_tokens,
            "cost_usd": result.cost_usd,
            "duration_seconds": result.duration_seconds,
        },
    ))

    logger.info(
        "Agent-Loop abgeschlossen: %d Iterationen, %d Tool-Aufrufe, %d Tokens, $%.4f",
        iteration + 1, len(result.tool_calls),
        result.total_tokens, result.cost_usd,
    )
    return result


def _fix_json_escapes(text: str) -> str:
    """Ersetzt ungueltige JSON-Backslash-Sequenzen durch doppelte Backslashes.

    Gueltige JSON-Escapes (\\n, \\t, \\r, \\b, \\f, \\\", \\\\, \\/, \\uXXXX)
    bleiben unveraendert. Alles andere (z.B. \\e, \\s, \\p) wird zu \\\\ + Zeichen.
    """
    return re.sub(
        r'\\(?!["\\/bfnrtu])',
        r'\\\\',
        text,
    )


def _strip_control_chars(text: str) -> str:
    """Entfernt rohe Steuerzeichen (ausser \\n, \\r, \\t) aus einem String.

    LLMs wie gpt-5.1 erzeugen manchmal JSON mit eingebetteten Control-Chars
    (z.B. \\x00-\\x1f), die json.loads(strict=True) ablehnt.
    """
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)


def load_llm_json(raw: str) -> dict:
    """Entfernt Code-Fences und parst den JSON-Anteil der LLM-Antwort.

    Repair-Kette:
    1. Normales json.loads (strict=False fuer eingebettete Newlines)
    2. Ungueltige Backslash-Sequenzen reparieren
    3. Rohe Steuerzeichen entfernen
    """
    cleaned = strip_code_fences(raw)
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        pass

    # Retry 1: ungueltige Backslash-Sequenzen reparieren (z.B. gpt-5.1 Output)
    try:
        fixed = _fix_json_escapes(cleaned)
        result = json.loads(fixed, strict=False)
        logger.warning("LLM-JSON enthielt ungueltige Escape-Sequenzen -- automatisch repariert")
        return result
    except json.JSONDecodeError:
        pass

    # Retry 2: Steuerzeichen entfernen + Escapes reparieren
    try:
        sanitized = _strip_control_chars(_fix_json_escapes(cleaned))
        result = json.loads(sanitized)
        logger.warning("LLM-JSON enthielt Steuerzeichen -- automatisch bereinigt")
        return result
    except json.JSONDecodeError as e3:
        logger.error("LLM-JSON konnte nicht geparst werden: %s\nRohe Ausgabe:\n%s", e3, raw)
        raise


def _clean(code: str) -> str:
    """Entfernt Markdown-Code-Bloecke und Prosa vor der ersten Python-Direktive."""
    # Schritt 1: Wenn ein eingebetteter ```python...```-Block existiert, diesen extrahieren
    m = re.search(r"```python\s*\n(.*?)```", code, flags=re.S)
    if m:
        code = m.group(1)
    else:
        code = re.sub(r"```.*?```", "", code, flags=re.S)
    # Schritt 2: Prosa vor der ersten Python-Direktive entfernen
    for i, line in enumerate(code.splitlines()):
        stripped = line.lstrip()
        if stripped.startswith(("import ", "from ", "def ", "class ", "# ", "try:", "if ", "with ")):
            return "\n".join(code.splitlines()[i:])
    return code.strip()


def run_python_code(raw_code: str) -> Tuple[str, str]:
    """Fuehrt generiertes Python-Skript als Subprocess aus. Gibt (stdout, stderr) zurueck."""
    script_code = _clean(raw_code)

    # UTF-8 erzwingen, damit Unicode-Zeichen auf Windows (cp1252) nicht abstuerzen
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    # libpysal/esda Warnungen unterdruecken (disconnected components, divide by zero)
    child_env["PYTHONWARNINGS"] = "ignore::UserWarning:libpysal,ignore::RuntimeWarning:esda"

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "gpt_script.py"
        tmp.write_text(script_code, encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(tmp)],
            capture_output=True,
            text=True,
            timeout=900,
            env=child_env,
        )
    return proc.stdout, proc.stderr


_driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD"))
)


def run_cypher(query: str) -> List[dict[str, Any]]:
    """Fuehrt eine Cypher-Abfrage aus und gibt die Ergebnisse als Liste von Dicts zurueck."""
    with _driver.session() as session:
        return [rec.data() for rec in session.run(query)]
