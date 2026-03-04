"""Datenklassen fuer die reichhaltige Chat-History.

Jede Chat-Nachricht speichert neben dem Text auch Analyseschritte,
Metriken und Internals, damit der Verlauf bei Streamlit-Reruns
vollstaendig reproduziert werden kann.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Truncation-Limits (Speicherschutz fuer Session-State)
# ---------------------------------------------------------------------------
MAX_STDOUT = 5000
MAX_STDERR = 5000
MAX_CODE = 10_000
MAX_PREVIEW = 10_000
MAX_TURNS = 50  # = 100 Nachrichten (User + Assistant)


def _truncate(text: str, limit: int) -> str:
    """Kuerzt einen String auf das Limit mit Hinweis."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (gekuerzt, {len(text)} Zeichen gesamt)"


# ---------------------------------------------------------------------------
# Disambiguation
# ---------------------------------------------------------------------------
@dataclass
class DisambiguationRecord:
    """Ergebnisse der Begriffsaufloesung eines Analyseschritts."""
    terms: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Einzelner Analyseschritt
# ---------------------------------------------------------------------------
@dataclass
class StepRecord:
    """UI-Abbild eines ausgefuehrten Analyseschritts.

    Wird aus StepResult (modules/chain.py) plus zusaetzlichen Daten
    (Code, Disambiguation) konstruiert.  StepResult selbst bleibt stabil.
    """
    step_index: int = 0
    analysis_type: str = ""
    decision_type: str = ""       # "cypher" | "python"
    sub_question: str = ""
    success: bool = False
    explanation: str = ""
    stdout: str = ""
    stderr: str = ""
    cypher_query: str = ""
    python_code: str = ""
    cypher_preview: str = ""      # JSON-String der Ergebnisvorschau
    geojson_path: str = ""
    skipped: bool = False
    skip_reason: str = ""
    disambiguation: Optional[DisambiguationRecord] = None

    def __post_init__(self) -> None:
        self.stdout = _truncate(self.stdout, MAX_STDOUT)
        self.stderr = _truncate(self.stderr, MAX_STDERR)
        self.cypher_query = _truncate(self.cypher_query, MAX_CODE)
        self.python_code = _truncate(self.python_code, MAX_CODE)
        self.cypher_preview = _truncate(self.cypher_preview, MAX_PREVIEW)


# ---------------------------------------------------------------------------
# Aggregierte Metriken
# ---------------------------------------------------------------------------
@dataclass
class MetricsRecord:
    """Aggregierte Token-/Kosten-/Dauer-Metriken einer Antwort."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    models_used: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Chat-Nachricht (User oder Assistant)
# ---------------------------------------------------------------------------
@dataclass
class ChatMessage:
    """Eine Nachricht im Chat-Verlauf mit allen zugehoerigen Daten."""
    role: str = "user"            # "user" | "assistant"
    timestamp: str = ""
    text: str = ""                # User-Frage oder Fehlermeldung
    plan_steps: list[dict] = field(default_factory=list)
    step_records: list[StepRecord] = field(default_factory=list)
    metrics: Optional[MetricsRecord] = None
    is_comparison: bool = False
    comparison_table: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# History-Verwaltung
# ---------------------------------------------------------------------------
def enforce_turn_limit(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Begrenzt die History auf MAX_TURNS Turns (User+Assistant-Paare).

    Entfernt die aeltesten Nachrichten paarweise.
    """
    if len(messages) <= MAX_TURNS * 2:
        return messages
    return messages[-(MAX_TURNS * 2):]
