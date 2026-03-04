"""Statistische Vergleichstests fuer Modellbewertung.

Friedman-Test (nicht-parametrisch, k verbundene Stichproben) mit
Nemenyi-Post-hoc-Test fuer paarweise Vergleiche.
Bloecke = Forschungsfragen, Gruppen = LLM-Modelle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import friedmanchisquare

from modules.logger import get_logger

logger = get_logger("debug")


# ---------------------------------------------------------------------------
# Ergebnis-Datenklassen
# ---------------------------------------------------------------------------
@dataclass
class NemenyiResult:
    """Ergebnis des Nemenyi-Post-hoc-Tests."""
    p_values: dict[tuple[str, str], float] = field(default_factory=dict)
    significant_pairs: list[tuple[str, str]] = field(default_factory=list)
    critical_difference: float = 0.0


@dataclass
class FriedmanResult:
    """Ergebnis des Friedman-Tests ueber mehrere Modelle und Fragen."""
    statistic: float = 0.0
    p_value: float = 1.0
    n_models: int = 0
    n_questions: int = 0
    is_significant: bool = False
    metric_name: str = ""
    rank_means: dict[str, float] = field(default_factory=dict)
    nemenyi: Optional[NemenyiResult] = None


# ---------------------------------------------------------------------------
# Friedman-Test
# ---------------------------------------------------------------------------
def run_friedman_test(
    data: pd.DataFrame,
    metric: str,
    models: list[str],
) -> Optional[FriedmanResult]:
    """Fuehrt den Friedman-Test auf einer Metrik ueber Modelle und Fragen aus.

    Args:
        data: DataFrame mit Spalten [question, model, <metric>].
              Jede Zeile ist der aggregierte (Median-)Wert eines Modells
              fuer eine Frage.
        metric: Name der zu vergleichenden Metrik
               (z.B. 'cost_usd', 'total_tokens', 'duration_seconds').
        models: Liste der Modellnamen.

    Returns:
        FriedmanResult oder None wenn nicht genug Daten.
    """
    if metric not in data.columns:
        logger.warning("Metrik '%s' nicht im DataFrame vorhanden", metric)
        return None

    # Nur Modelle behalten, die im DataFrame vorkommen
    available_models = [m for m in models if m in data["model"].unique()]
    if len(available_models) < 3:
        logger.warning(
            "Friedman-Test erfordert mindestens 3 Gruppen, nur %d vorhanden",
            len(available_models),
        )
        return None

    # Pivot: Fragen als Zeilen (Bloecke), Modelle als Spalten
    subset = data[data["model"].isin(available_models)][["question", "model", metric]]
    pivot = subset.pivot(index="question", columns="model", values=metric)

    # Nur Fragen behalten, fuer die ALLE Modelle einen Wert haben
    pivot = pivot.dropna()

    if len(pivot) < 2:
        logger.warning(
            "Zu wenig vollstaendige Fragenpaare fuer Friedman-Test (%d)",
            len(pivot),
        )
        return None

    # Friedman-Test ausfuehren
    groups = [pivot[m].values for m in available_models if m in pivot.columns]
    stat, p_val = friedmanchisquare(*groups)

    # NaN-Schutz: bei identischen Werten liefert Friedman NaN
    if np.isnan(stat) or np.isnan(p_val):
        stat = 0.0
        p_val = 1.0

    # Rang-Mittelwerte berechnen
    ranks = pivot[available_models].rank(axis=1)
    rank_means = {
        m: float(ranks[m].mean())
        for m in available_models
        if m in ranks.columns
    }

    result = FriedmanResult(
        statistic=float(stat),
        p_value=float(p_val),
        n_models=len(available_models),
        n_questions=len(pivot),
        is_significant=bool(p_val < 0.05),
        metric_name=metric,
        rank_means=rank_means,
    )

    # Nemenyi nur bei signifikantem Friedman-Ergebnis
    if result.is_significant:
        result.nemenyi = _run_nemenyi(pivot, available_models)

    return result


# ---------------------------------------------------------------------------
# Nemenyi-Post-hoc
# ---------------------------------------------------------------------------
def _run_nemenyi(
    pivot: pd.DataFrame,
    models: list[str],
) -> NemenyiResult:
    """Fuehrt den Nemenyi-Post-hoc-Test nach signifikantem Friedman aus."""
    import scikit_posthocs as sp

    model_cols = [m for m in models if m in pivot.columns]
    matrix = pivot[model_cols].values

    # Nemenyi-Friedman-Test
    nemenyi_df = sp.posthoc_nemenyi_friedman(matrix)

    # p-Werte extrahieren
    p_values: dict[tuple[str, str], float] = {}
    significant_pairs: list[tuple[str, str]] = []

    for i, m1 in enumerate(model_cols):
        for j, m2 in enumerate(model_cols):
            if i < j:
                p = float(nemenyi_df.iloc[i, j])
                p_values[(m1, m2)] = p
                if p < 0.05:
                    significant_pairs.append((m1, m2))

    # Kritische Differenz (CD) berechnen
    k = len(model_cols)
    n = len(pivot)
    # q_alpha fuer Nemenyi: q_alpha / sqrt(2) * sqrt(k*(k+1) / (6*n))
    # Vereinfachte Berechnung via scipy
    from scipy.stats import studentized_range
    q_alpha = studentized_range.ppf(0.95, k, np.inf)
    cd = q_alpha * np.sqrt(k * (k + 1) / (12 * n))

    return NemenyiResult(
        p_values=p_values,
        significant_pairs=significant_pairs,
        critical_difference=cd,
    )


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def build_friedman_dataframe(run_rows: list[dict]) -> pd.DataFrame:
    """Baut einen DataFrame aus Benchmark-Runs fuer den Friedman-Test.

    Aggregiert mehrere Runs pro Modell/Frage zum Median und liefert
    eine Zeile pro (question, model)-Kombination.
    """
    if not run_rows:
        return pd.DataFrame()

    df = pd.DataFrame(run_rows)

    # Numerische Spalten identifizieren
    numeric_cols = [
        "success", "prompt_tokens", "completion_tokens",
        "reasoning_tokens", "total_tokens", "cost_usd",
        "duration_seconds",
    ]
    available_numeric = [c for c in numeric_cols if c in df.columns]

    # Median pro (question, model)
    grouped = df.groupby(["question", "model"])[available_numeric].median().reset_index()
    return grouped
