#!/usr/bin/env python3
"""Headless Benchmark-Skript fuer den Modellvergleich.

Fuehrt vordefinierte Forschungsfragen an alle konfigurierten Modelle aus
(N Durchlaeufe pro Modell pro Frage), sammelt Metriken, und fuehrt
Friedman/Nemenyi-Tests auf den aggregierten Daten aus.

Verwendet den agentischen Tool-Use-Loop (run_agent) aus modules/llm.py.

Ausfuehrung:
    python scripts/benchmark.py
    python scripts/benchmark.py --n-runs 3 --output-dir results/benchmark
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Projekt-Root zu sys.path hinzufuegen
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "app"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import numpy as np

from modules.helper import (
    drain_llm_results,
    AgentResult,
    ToolCallRecord,
)
from modules.llm import run_agent
from modules.disambiguator import drain_disambiguation_results
from modules.statistics import (
    run_friedman_test,
    build_friedman_dataframe,
    FriedmanResult,
)
from modules.logger import get_logger

logger = get_logger("debug")


# ---------------------------------------------------------------------------
# Forschungsfragen (manuell befuellen)
# ---------------------------------------------------------------------------
RESEARCH_QUESTIONS: list[str] = [
    "Gibt es eine statistisch signifikante Kolokation zwischen Features mit Category = 'tumulus' und Location1 = 'ridge' und solchen mit Category IN ['habitation site', 'hut', 'settlement']?",
    "Gibt es eine signifikante räumliche Autokorrelation von Sesshaftigkeitsindikatoren (categories: habitation site; hut; settlement) bzw. Mobilitätsindikatoren (shelter; stoneplace; campsite; fireplace; gravel platform)?",
    "Gibt es statistisch signifikante Kolokationen von Sesshaftigkeits-  bzw. Mobilitätsindikatoren mit der Größe (Anzahl von Features) von Friedhöfen (categories: box grave; cleft burial; grave; dome grave; tumulus) und deren Abstand zu einander?",
    "Gibt es eine statistisch signifikante Kolokation zwischen Sesshaftigkeitsindikatoren (categories: habitation site; hut; settlement) und Brunnen (category: well)?",
    "Gibt es eine statistisch signifikante Kolokation zwischen Mobilitäts- bzw. Sesshaftigkeitsindikatoren (categories: habitation site; hut; settlement) und der Feature-Category 'rock art'?"
]


# ---------------------------------------------------------------------------
# Metriken-Extraktion aus AgentResult
# ---------------------------------------------------------------------------
_MORAN_KEYS = ("moran_I", "moran_bv_I", "I", "morans_i", "bivariate_moran_I")
_P_KEYS = ("p_value", "p_sim", "p_norm", "p")
_N_KEYS = ("n", "n_units", "n_observations")


def _find_first(d: dict, keys: tuple[str, ...]) -> object:
    """Gibt den ersten vorhandenen Wert fuer eine der Keys zurueck."""
    for k in keys:
        if k in d:
            return d[k]
    return None


def _extract_summary_values(summary_json: dict | None) -> dict:
    """Extrahiert moran_I, p_value, n aus summary_json (top-level oder verschachtelt)."""
    if not summary_json:
        return {"moran_I": None, "p_value": None, "n": None}

    moran_i = _find_first(summary_json, _MORAN_KEYS)
    p_value = _find_first(summary_json, _P_KEYS)
    n = _find_first(summary_json, _N_KEYS)

    # Verschachtelte Suche falls top-level nichts ergab
    if moran_i is None:
        for v in summary_json.values():
            if isinstance(v, dict):
                mi = _find_first(v, _MORAN_KEYS)
                if mi is not None:
                    moran_i = mi
                    p_value = p_value or _find_first(v, _P_KEYS)
                    n = n or _find_first(v, _N_KEYS)
                    break

    return {"moran_I": moran_i, "p_value": p_value, "n": n}


def _extract_values_from_stdout(stdout: str) -> dict:
    """Fallback: Extrahiert moran_I, p_value, n via Regex aus stdout."""
    result: dict = {"moran_I": None, "p_value": None, "n": None}
    if not stdout:
        return result

    m = re.search(
        r"Moran(?:'s)?\s*(?:BV\s*)?I\s*(?:\([^)]*\))?\s*[:=]\s*(-?[\d.]+(?:e[+-]?\d+)?)",
        stdout, re.IGNORECASE,
    )
    if not m:
        m = re.search(r"moran_(?:bv_)?[Ii]\s*[:=]\s*(-?[\d.]+(?:e[+-]?\d+)?)", stdout)
    if m:
        try:
            result["moran_I"] = float(m.group(1))
        except ValueError:
            pass

    m = re.search(
        r"(?:p[_-]?(?:value|sim|norm|wert)|P-Wert)\s*(?:\([^)]*\))?\s*[:=]\s*([\d.]+(?:e[+-]?\d+)?)",
        stdout, re.IGNORECASE,
    )
    if m:
        try:
            result["p_value"] = float(m.group(1))
        except ValueError:
            pass

    for pattern in [
        r"Analyse-Einheiten\s*[:=]\s*(\d+)",
        r"(?:Number of |Anzahl\s+)?(?:sites?|Einheiten)\s+(?:analyzed|analysiert|untersucht)\s*[:=]?\s*(\d+)",
        r"(?:n_units|n_observations|n)\s*[:=]\s*(\d+)",
        r"(\d+)\s+(?:sites?\b|Fundstellen|Einheiten)",
    ]:
        m = re.search(pattern, stdout, re.IGNORECASE)
        if m:
            try:
                result["n"] = int(m.group(1))
                break
            except ValueError:
                continue

    return result


def _extract_metrics_from_agent(result: AgentResult) -> dict:
    """Extrahiert alle relevanten Felder aus einem AgentResult fuer den Benchmark."""
    # Analyse-Typ und Decision-Typ aus Tool-Aufrufen ableiten
    analysis_type = ""
    decision_type = "agent"
    cypher_query = ""
    python_code = ""
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    geojson_path = ""
    summary_json = None

    for tc in result.tool_calls:
        if tc.tool_name == "run_cypher_query":
            cypher_query = tc.cypher_query or cypher_query
        elif tc.tool_name == "run_spatial_analysis":
            python_code = tc.python_code or python_code
            analysis_type = tc.arguments.get("analysis_type", "") or analysis_type
            geojson_path = tc.geojson_path or geojson_path
            summary_json = tc.summary_json or summary_json

        if tc.stdout:
            stdout_parts.append(tc.stdout)
        if tc.stderr:
            stderr_parts.append(tc.stderr)

    success = all(tc.success for tc in result.tool_calls) if result.tool_calls else bool(result.answer)

    stdout = "\n".join(stdout_parts).strip()
    stderr = "\n".join(stderr_parts).strip()

    # Summary-Werte extrahieren
    summary_vals = _extract_summary_values(summary_json)
    if summary_vals["moran_I"] is None:
        stdout_vals = _extract_values_from_stdout(stdout)
        for k in ("moran_I", "p_value", "n"):
            if summary_vals[k] is None and stdout_vals[k] is not None:
                summary_vals[k] = stdout_vals[k]

    return {
        "success": success,
        "analysis_type": analysis_type,
        "decision_type": decision_type,
        "explanation": result.answer,
        "cypher_query": cypher_query,
        "python_code": python_code,
        "stdout": stdout,
        "stderr": stderr,
        "geojson_path": geojson_path,
        "summary_json": summary_json,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "reasoning_tokens": result.reasoning_tokens,
        "total_tokens": result.total_tokens,
        "cost_usd": result.cost_usd,
        "duration_seconds": result.duration_seconds,
        "moran_I": summary_vals["moran_I"],
        "p_value": summary_vals["p_value"],
        "n": summary_vals["n"],
    }


# ---------------------------------------------------------------------------
# Benchmark-Hauptlogik
# ---------------------------------------------------------------------------
_print_lock = threading.Lock()


def _tprint(*args, **kwargs) -> None:
    """Thread-safe print."""
    with _print_lock:
        print(*args, **kwargs)


def _load_existing_runs(result_dir: Path) -> list[dict]:
    """Laedt bestehende Runs aus einem Ergebnis-Verzeichnis."""
    runs_json = result_dir / "runs.json"
    if not runs_json.exists():
        return []
    try:
        with open(runs_json, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("runs", [])
    except Exception as e:
        logger.error("Bestehende Runs konnten nicht geladen werden: %s", e)
        return []


def _run_single(
    question: str,
    model_name: str,
    run_idx: int,
    q_idx: int,
    result_dir: Path,
    label: str,
) -> dict:
    """Fuehrt einen einzelnen Benchmark-Durchlauf aus (thread-safe).

    Verwendet run_agent() fuer den agentischen Loop.
    Alle LLM/Disambiguierungs-Puffer sind thread-lokal, daher ist diese
    Funktion sicher fuer den Einsatz in ThreadPoolExecutor.
    """
    _tprint(f"\n  [{label}] {model_name} -- Run {run_idx + 1}")

    drain_llm_results()
    drain_disambiguation_results()

    data_path = str(
        result_dir / f"input_{model_name}_{q_idx}_run{run_idx}.json"
    )

    # Agentischen Loop ausfuehren
    result = run_agent(question, model=model_name, data_path=data_path)

    # Metriken und Ergebnisse extrahieren
    extracted = _extract_metrics_from_agent(result)

    run_record = {
        "question": question,
        "model": model_name,
        "run_index": run_idx,
        "success": 1 if extracted["success"] else 0,
        "analysis_type": extracted["analysis_type"],
        "decision_type": extracted["decision_type"],
        "explanation": extracted["explanation"],
        "prompt_tokens": extracted["prompt_tokens"],
        "completion_tokens": extracted["completion_tokens"],
        "reasoning_tokens": extracted["reasoning_tokens"],
        "total_tokens": extracted["total_tokens"],
        "cost_usd": extracted["cost_usd"],
        "duration_seconds": extracted["duration_seconds"],
        "moran_I": extracted["moran_I"],
        "p_value": extracted["p_value"],
        "n": extracted["n"],
        "stdout": extracted["stdout"],
        "stderr": extracted["stderr"],
        "python_code": extracted["python_code"],
        "cypher_query": extracted["cypher_query"],
    }

    status = "OK" if extracted["success"] else "FEHLER"
    _tprint(f"    [{label}] {model_name} Run {run_idx + 1}: {status} | "
            f"Tokens: {extracted['total_tokens']} | "
            f"${extracted['cost_usd']:.4f} | "
            f"{extracted['duration_seconds']:.1f}s")
    if not extracted["success"] and extracted["stderr"]:
        _tprint(f"    [{label}] Fehler: {extracted['stderr'][:200]}")

    return run_record


def run_benchmark(
    questions: list[str],
    models: list[str],
    n_runs: int = 3,
    output_dir: str = "results/benchmark",
    append_to: str | None = None,
    parallel: bool = False,
) -> Path:
    """Fuehrt den vollstaendigen Benchmark aus.

    Args:
        questions: Liste der Forschungsfragen.
        models: Liste der Modellnamen.
        n_runs: Anzahl Durchlaeufe pro Modell pro Frage.
        output_dir: Ausgabeverzeichnis.
        append_to: Pfad zu bestehendem Ergebnis-Verzeichnis (fuer Nachtrag).
        parallel: Wenn True, werden Modelle pro Frage parallel ausgefuehrt.

    Returns:
        Pfad zum Ergebnis-Verzeichnis.
    """
    if append_to:
        result_dir = Path(append_to)
        if not result_dir.exists():
            print(f"FEHLER: Verzeichnis existiert nicht: {result_dir}")
            sys.exit(1)
        ts = result_dir.name
        existing_runs = _load_existing_runs(result_dir)
        print(f"Append-Modus: {len(existing_runs)} bestehende Runs geladen aus {result_dir}")
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        result_dir = Path(output_dir) / ts
        result_dir.mkdir(parents=True, exist_ok=True)
        existing_runs = []

    all_runs: list[dict] = list(existing_runs)
    total = len(questions) * len(models) * n_runs

    if parallel:
        print(f"  Modus: PARALLEL ({len(models)} Modelle gleichzeitig)")

    for q_idx, question in enumerate(questions):
        print(f"\n{'='*60}")
        print(f"Frage {q_idx + 1}/{len(questions)}: {question[:80]}")
        print(f"{'='*60}")

        # Alle (model, run_idx)-Kombinationen fuer diese Frage
        tasks = [
            (model_name, run_idx)
            for model_name in models
            for run_idx in range(n_runs)
        ]

        if parallel:
            with ThreadPoolExecutor(max_workers=len(models)) as executor:
                futures = {}
                for i, (model_name, run_idx) in enumerate(tasks):
                    label = f"F{q_idx+1} {i+1}/{len(tasks)}"
                    fut = executor.submit(
                        _run_single,
                        question=question,
                        model_name=model_name,
                        run_idx=run_idx,
                        q_idx=q_idx,
                        result_dir=result_dir,
                        label=label,
                    )
                    futures[fut] = (model_name, run_idx)

                for fut in as_completed(futures):
                    try:
                        record = fut.result()
                        all_runs.append(record)
                    except Exception as exc:
                        model_name, run_idx = futures[fut]
                        _tprint(f"    FEHLER {model_name} Run {run_idx}: {exc}")
                        logger.exception("Benchmark-Run fehlgeschlagen: %s", exc)
        else:
            for i, (model_name, run_idx) in enumerate(tasks):
                label = f"F{q_idx+1} {i+1}/{len(tasks)}"
                record = _run_single(
                    question=question,
                    model_name=model_name,
                    run_idx=run_idx,
                    q_idx=q_idx,
                    result_dir=result_dir,
                    label=label,
                )
                all_runs.append(record)

    # --- Ergebnisse speichern ---
    all_models = sorted({r["model"] for r in all_runs})
    _save_runs_csv(result_dir / "runs.csv", all_runs)
    _save_runs_json(result_dir / "runs.json", all_runs, ts, all_models, n_runs)
    _save_aggregated_csv(result_dir / "aggregated.csv", all_runs)

    # --- Friedman/Nemenyi ---
    friedman_summary = _run_friedman_analysis(all_runs, all_models)
    _save_friedman_summary(
        result_dir / "friedman_summary.json", friedman_summary, ts, all_models, n_runs,
    )

    print(f"\n{'='*60}")
    print(f"Benchmark abgeschlossen. Ergebnisse: {result_dir}")
    print(f"  runs.csv            -- {len(all_runs)} Einzel-Durchlaeufe")
    print(f"  runs.json           -- Vollstaendige Daten")
    print(f"  aggregated.csv      -- Median-Werte pro Modell/Frage")
    print(f"  friedman_summary.json -- Statistische Tests")
    print(f"{'='*60}")

    return result_dir


# ---------------------------------------------------------------------------
# Persistenz
# ---------------------------------------------------------------------------
CSV_FIELDNAMES = [
    "question", "model", "run_index", "success", "analysis_type",
    "decision_type", "prompt_tokens", "completion_tokens",
    "reasoning_tokens", "total_tokens", "cost_usd", "duration_seconds",
    "moran_I", "p_value", "n", "explanation", "stderr",
]


def _save_runs_csv(path: Path, runs: list[dict]) -> None:
    """Speichert alle Einzeldurchlaeufe als CSV."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for run in runs:
            row = {k: run.get(k, "") for k in CSV_FIELDNAMES}
            row = {k: ("" if v is None else v) for k, v in row.items()}
            writer.writerow(row)


def _save_runs_json(
    path: Path,
    runs: list[dict],
    timestamp: str,
    models: list[str],
    n_runs: int,
) -> None:
    """Speichert alle Durchlaeufe als JSON mit Metadaten."""
    record = {
        "timestamp": timestamp,
        "n_models": len(models),
        "n_questions": len({r["question"] for r in runs}),
        "n_runs_per_model": n_runs,
        "models": models,
        "questions": list({r["question"] for r in runs}),
        "runs": runs,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False, default=str)


def _save_aggregated_csv(path: Path, runs: list[dict]) -> None:
    """Speichert Median-Werte pro Modell pro Frage als CSV."""
    import pandas as pd

    df = pd.DataFrame(runs)
    if df.empty:
        return

    numeric_cols = [
        "success", "prompt_tokens", "completion_tokens",
        "reasoning_tokens", "total_tokens", "cost_usd", "duration_seconds",
        "moran_I", "p_value", "n",
    ]
    available = [c for c in numeric_cols if c in df.columns]

    grouped = df.groupby(["question", "model"])[available].median().reset_index()
    grouped.to_csv(path, index=False)


# ---------------------------------------------------------------------------
# Friedman-Analyse
# ---------------------------------------------------------------------------
def _run_friedman_analysis(
    runs: list[dict],
    models: list[str],
) -> dict:
    """Fuehrt Friedman/Nemenyi auf allen gesammelten Runs aus."""
    df = build_friedman_dataframe(runs)
    if df.empty:
        return {}

    metrics_to_test = [
        ("cost_usd", "Kosten (USD)"),
        ("total_tokens", "Tokens (gesamt)"),
        ("duration_seconds", "Dauer (s)"),
        ("success", "Erfolgsrate"),
    ]

    results: dict = {}
    for metric_key, metric_label in metrics_to_test:
        if metric_key not in df.columns:
            continue

        fr = run_friedman_test(df, metric_key, models)
        if fr is None:
            continue

        entry: dict = {
            "label": metric_label,
            "statistic": fr.statistic,
            "p_value": fr.p_value,
            "is_significant": fr.is_significant,
            "n_models": fr.n_models,
            "n_questions": fr.n_questions,
            "rank_means": fr.rank_means,
        }

        if fr.nemenyi:
            entry["nemenyi"] = {
                "p_values": {
                    f"{m1} vs {m2}": p
                    for (m1, m2), p in fr.nemenyi.p_values.items()
                },
                "significant_pairs": [
                    list(pair) for pair in fr.nemenyi.significant_pairs
                ],
                "critical_difference": fr.nemenyi.critical_difference,
            }

        results[metric_key] = entry

        # Konsolenausgabe
        sig = "SIGNIFIKANT" if fr.is_significant else "nicht signifikant"
        print(f"\n  Friedman ({metric_label}): chi2={fr.statistic:.3f}, "
              f"p={fr.p_value:.4f} -- {sig}")
        print(f"    Rang-Mittelwerte: {fr.rank_means}")
        if fr.nemenyi and fr.nemenyi.significant_pairs:
            print(f"    Signifikante Paare (Nemenyi):")
            for m1, m2 in fr.nemenyi.significant_pairs:
                p = fr.nemenyi.p_values[(m1, m2)]
                print(f"      {m1} vs {m2}: p={p:.4f}")

    return results


def _save_friedman_summary(
    path: Path,
    friedman_results: dict,
    timestamp: str,
    models: list[str],
    n_runs: int,
) -> None:
    """Speichert Friedman/Nemenyi-Ergebnisse als JSON."""
    record = {
        "timestamp": timestamp,
        "n_runs_per_model": n_runs,
        "models": models,
        "metrics": friedman_results,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CLI-Einstiegspunkt
# ---------------------------------------------------------------------------
def _get_all_model_names() -> list[str]:
    """Laedt alle Modellnamen aus der Registry."""
    from modules.helper import MODEL_REGISTRY
    return list(MODEL_REGISTRY.keys())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Headless Benchmark fuer LLM-Modellvergleich mit Friedman/Nemenyi",
    )
    parser.add_argument(
        "--n-runs", type=int, default=3,
        help="Anzahl Durchlaeufe pro Modell pro Frage (default: 3)",
    )
    parser.add_argument(
        "--output-dir", type=str, default="results/benchmark",
        help="Ausgabeverzeichnis (default: results/benchmark)",
    )
    parser.add_argument(
        "--models", type=str, nargs="*", default=None,
        help="Modelle (default: alle aus models.yml)",
    )
    parser.add_argument(
        "--append-to", type=str, default=None,
        help="Pfad zu bestehendem Ergebnis-Verzeichnis fuer Nachtrag "
             "(z.B. results/benchmark/20260302_185914)",
    )
    parser.add_argument(
        "--parallel", action="store_true", default=False,
        help="Modelle parallel ausfuehren (default: sequentiell)",
    )
    args = parser.parse_args()

    if not RESEARCH_QUESTIONS:
        print("FEHLER: Keine Forschungsfragen definiert.")
        print("Bitte RESEARCH_QUESTIONS in scripts/benchmark.py befuellen.")
        sys.exit(1)

    models = args.models if args.models else _get_all_model_names()
    mode = "parallel" if args.parallel else "sequentiell"
    print(f"Benchmark-Konfiguration:")
    print(f"  Modelle:      {models}")
    print(f"  Fragen:       {len(RESEARCH_QUESTIONS)}")
    print(f"  Runs/Modell:  {args.n_runs}")
    print(f"  Gesamt-Runs:  {len(RESEARCH_QUESTIONS) * len(models) * args.n_runs}")
    print(f"  Modus:        {mode}")
    print(f"  Ausgabe:      {args.output_dir}")

    run_benchmark(
        questions=RESEARCH_QUESTIONS,
        models=models,
        n_runs=args.n_runs,
        output_dir=args.output_dir,
        append_to=args.append_to,
        parallel=args.parallel,
    )


if __name__ == "__main__":
    main()
