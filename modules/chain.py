"""Datenklassen fuer Analyseschritte.

Minimale Datenstrukturen fuer die Ergebnisaufzeichnung.
Die eigentliche Ablaufsteuerung erfolgt jetzt ueber den agentischen
LLM-Loop in modules/llm.py (Tool-Use).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StepResult:
    """Ergebnis eines einzelnen Analyseschritts (Benchmark-kompatibel)."""
    step_index: int = 0
    analysis_type: str = ""
    success: bool = False
    stdout: str = ""
    stderr: str = ""
    explanation: str = ""
    summary_json: Optional[dict] = None
    data_path: str = ""
    geojson_path: str = ""
    skipped: bool = False
    skip_reason: str = ""
