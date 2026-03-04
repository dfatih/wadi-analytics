#!/usr/bin/env python3
"""Schnelltest aller 5 Kontrollfragen -- parallel, mit Debug-Output.

Fuehrt alle 5 Forschungsfragen einmal mit dem Default-Modell aus,
sammelt vollstaendige Debug-Informationen (Cypher, Python-Code,
stdout, stderr, summary-JSON) und vergleicht die Ergebnisse mit
den erwarteten Antworten.

Ausfuehrung:
    python scripts/test_kontrollfragen.py
    python scripts/test_kontrollfragen.py --sequential
    python scripts/test_kontrollfragen.py --cell-size 5000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Projekt-Root zu sys.path hinzufuegen
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "app"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from modules.helper import drain_llm_results, AgentResult, ToolCallRecord, DEFAULT_MODEL
from modules.llm import run_agent
from modules.disambiguator import drain_disambiguation_results
from modules.logger import get_logger

logger = get_logger("debug")

_print_lock = threading.Lock()


def _tprint(*args, **kwargs) -> None:
    """Thread-safe print."""
    with _print_lock:
        print(*args, **kwargs, flush=True)


# ---------------------------------------------------------------------------
# Kontrollfragen + erwartete Ergebnisse
# ---------------------------------------------------------------------------
KONTROLLFRAGEN: list[dict] = [
    {
        "id": "Q1",
        "label": "Kolokation Ridge-Tumuli <-> Settlements",
        "question": (
            "Gibt es eine statistisch signifikante Kolokation zwischen "
            "Features mit Category = 'tumulus' und Location1 = 'ridge' "
            "und solchen mit Category IN ['habitation site', 'hut', 'settlement']?"
        ),
        "expected": {
            "significant": False,
            "p_threshold": 0.05,
            "hotspot_lon_range": [32.08, 32.62],
            "notes": "Messbare Kolokation, aber p > 0.05. Hotspot 32d05E-32d37E.",
        },
    },
    {
        "id": "Q2",
        "label": "Autokorrelation Sesshaftigkeit/Mobilitaet",
        "question": (
            "Gibt es eine signifikante raeumliche Autokorrelation von "
            "Sesshaftigkeitsindikatoren (categories: habitation site; hut; "
            "settlement) bzw. Mobilitaetsindikatoren (shelter; stoneplace; "
            "camp site; fireplace; gravel platform)?"
        ),
        "expected": {
            "significant": True,
            "cluster_region": [31.92, 32.08],
            "notes": (
                "Beide Gruppen signifikant. Cluster vor allem 31d55E-32d05E, "
                "aber nur in einigen Gelaendeabschnitten."
            ),
        },
    },
    {
        "id": "Q3",
        "label": "Friedhofsgroesse/-abstand vs. Indikatoren",
        "question": (
            "Gibt es statistisch signifikante Kolokationen von Sesshaftigkeits- "
            "bzw. Mobilitaetsindikatoren mit der Groesse (Anzahl von Features) "
            "von Friedhoefen (categories: box graves; cleft burial; grave; "
            "dome grave; tumulus) und deren Abstand zu einander?"
        ),
        "expected": {
            "size_sedentary": {"sign": "negative", "significant": True},
            "dist_sedentary": {"sign": "negative", "significant": True},
            "mobility": {"significant": False},
            "notes": "Negative Kolokation Groesse+Abstand vs Sesshaftigkeit. Mobilitaet nicht signifikant.",
        },
    },
    {
        "id": "Q4",
        "label": "Sesshaftigkeit <-> Brunnen",
        "question": (
            "Gibt es eine statistisch signifikante Kolokation zwischen "
            "Sesshaftigkeitsindikatoren (categories: habitation site; hut; "
            "settlement) und Brunnen (category: well)?"
        ),
        "expected": {
            "significant": True,
            "sign": "positive",
            "notes": "Deutlich signifikante Kolokation.",
        },
    },
    {
        "id": "Q5",
        "label": "Rock Art <-> Sesshaftigkeit/Mobilitaet",
        "question": (
            "Gibt es eine statistisch signifikante Kolokation zwischen "
            "Mobilitaets- bzw. Sesshaftigkeitsindikatoren (categories: "
            "habitation site; hut; settlement) und der Feature-Category "
            "'rock art'?"
        ),
        "expected": {
            "sedentary_significant": True,
            "mobility_significant": False,
            "notes": "Sesshaftigkeit signifikant, Mobilitaet nicht.",
        },
    },
]


# ---------------------------------------------------------------------------
# Debug-Extraktion aus AgentResult
# ---------------------------------------------------------------------------
def _extract_debug(result: AgentResult) -> dict:
    """Extrahiert alle Debug-relevanten Felder aus einem AgentResult."""
    steps: list[dict] = []
    for i, tc in enumerate(result.tool_calls):
        step = {
            "step_index": i + 1,
            "tool_name": tc.tool_name,
            "success": tc.success,
            "arguments": tc.arguments,
            "cypher_query": tc.cypher_query or None,
            "python_code": tc.python_code or None,
            "stdout": tc.stdout or None,
            "stderr": tc.stderr or None,
            "geojson_path": tc.geojson_path or None,
            "summary_json": tc.summary_json or None,
        }
        steps.append(step)

    return {
        "answer": result.answer,
        "n_steps": len(result.tool_calls),
        "steps": steps,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "reasoning_tokens": result.reasoning_tokens,
        "total_tokens": result.total_tokens,
        "cost_usd": result.cost_usd,
        "duration_seconds": result.duration_seconds,
        "model": result.model,
    }


# ---------------------------------------------------------------------------
# Einzelne Frage ausfuehren
# ---------------------------------------------------------------------------
def _run_question(
    kf: dict,
    cell_size: int,
    result_dir: Path,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Fuehrt eine Kontrollfrage aus und gibt das vollstaendige Ergebnis zurueck."""
    qid = kf["id"]
    _tprint(f"\n  [{qid}] Starte: {kf['label']}")

    drain_llm_results()
    drain_disambiguation_results()

    data_path = str(result_dir / f"input_{qid}.json")
    t0 = time.time()

    try:
        agent_result = run_agent(
            kf["question"],
            model=model,
            data_path=data_path,
            cell_size=cell_size,
        )
        debug = _extract_debug(agent_result)
        success = True
        error = None
    except Exception as exc:
        logger.exception("Fehler bei %s: %s", qid, exc)
        debug = {}
        success = False
        error = str(exc)

    elapsed = time.time() - t0

    # Ergebnis zusammenbauen
    record = {
        "id": qid,
        "label": kf["label"],
        "question": kf["question"],
        "expected": kf["expected"],
        "success": success,
        "error": error,
        "duration_seconds": round(elapsed, 2),
        "debug": debug,
    }

    if success:
        _tprint(
            f"  [{qid}] Fertig in {elapsed:.1f}s | "
            f"Tokens: {debug.get('total_tokens', 0)} | "
            f"${debug.get('cost_usd', 0):.4f} | "
            f"Steps: {debug.get('n_steps', 0)}"
        )
        # Kurzfassung der Antwort
        answer = debug.get("answer", "")
        if answer:
            _tprint(f"  [{qid}] Antwort: {answer[:200]}...")
    else:
        _tprint(f"  [{qid}] FEHLER: {error}")

    return record


# ---------------------------------------------------------------------------
# Hauptlogik
# ---------------------------------------------------------------------------
def run_all(
    cell_size: int = 2000,
    sequential: bool = False,
    model: str = DEFAULT_MODEL,
) -> Path:
    """Fuehrt alle Kontrollfragen aus und speichert Ergebnisse."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    result_dir = Path("results") / "kontrollfragen" / ts
    result_dir.mkdir(parents=True, exist_ok=True)

    print(f"Kontrollfragen-Test")
    print(f"  Modell:       {model}")
    print(f"  Cell-Size:    {cell_size}m")
    print(f"  Modus:        {'sequentiell' if sequential else 'parallel'}")
    print(f"  Ausgabe:      {result_dir}")
    print(f"  Fragen:       {len(KONTROLLFRAGEN)}")
    print(f"{'='*60}")

    results: list[dict] = []

    if sequential:
        for kf in KONTROLLFRAGEN:
            record = _run_question(kf, cell_size, result_dir, model=model)
            results.append(record)
    else:
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {}
            for kf in KONTROLLFRAGEN:
                fut = executor.submit(
                    _run_question, kf, cell_size, result_dir, model=model,
                )
                futures[fut] = kf["id"]

            for fut in as_completed(futures):
                qid = futures[fut]
                try:
                    record = fut.result()
                    results.append(record)
                except Exception as exc:
                    _tprint(f"  [{qid}] FEHLER: {exc}")
                    results.append({
                        "id": qid,
                        "success": False,
                        "error": str(exc),
                    })

    # Nach ID sortieren
    results.sort(key=lambda r: r.get("id", ""))

    # --- Zusammenfassung ---
    _print_summary(results)

    # --- Speichern ---
    output = {
        "timestamp": ts,
        "model": model,
        "cell_size": cell_size,
        "results": results,
    }
    output_path = result_dir / "kontrollfragen.json"
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False, default=str)

    print(f"\nErgebnisse gespeichert: {output_path}")
    return result_dir


def _print_summary(results: list[dict]) -> None:
    """Druckt eine kompakte Zusammenfassung aller Ergebnisse."""
    print(f"\n{'='*60}")
    print("ZUSAMMENFASSUNG")
    print(f"{'='*60}")

    total_cost = 0.0
    total_tokens = 0
    total_duration = 0.0

    for r in results:
        qid = r.get("id", "?")
        label = r.get("label", "")
        success = r.get("success", False)
        debug = r.get("debug", {})
        expected = r.get("expected", {})

        status = "OK" if success else "FEHLER"
        cost = debug.get("cost_usd", 0) or 0
        tokens = debug.get("total_tokens", 0) or 0
        duration = r.get("duration_seconds", 0) or 0

        total_cost += cost
        total_tokens += tokens
        total_duration += duration

        print(f"\n  {qid}: {label}")
        print(f"    Status:   {status}")
        print(f"    Dauer:    {duration:.1f}s | Tokens: {tokens} | ${cost:.4f}")

        if success and debug:
            answer = debug.get("answer", "")
            if answer:
                # Erste 300 Zeichen der Antwort
                print(f"    Antwort:  {answer[:300]}")

            # Schritte auflisten
            for step in debug.get("steps", []):
                idx = step["step_index"]
                tool = step["tool_name"]
                ok = "OK" if step["success"] else "FAIL"
                print(f"    Step {idx}: {tool} [{ok}]")

                if step.get("cypher_query"):
                    # Erste Zeile der Cypher-Query
                    cq = step["cypher_query"].replace("\n", " ")[:120]
                    print(f"      Cypher: {cq}...")
                if step.get("stdout"):
                    # Letzte Zeilen des stdout (die relevanten Ergebnisse)
                    lines = step["stdout"].strip().split("\n")
                    for line in lines[-5:]:
                        print(f"      > {line}")
                if step.get("stderr") and not step["success"]:
                    err = step["stderr"][:200]
                    print(f"      STDERR: {err}")
                if step.get("summary_json"):
                    sj = json.dumps(step["summary_json"], indent=None, ensure_ascii=False)
                    if len(sj) > 200:
                        sj = sj[:200] + "..."
                    print(f"      Summary: {sj}")

        print(f"    Erwartet: {expected.get('notes', '')}")

    print(f"\n{'='*60}")
    print(f"Gesamt: {total_duration:.1f}s | {total_tokens} Tokens | ${total_cost:.4f}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Schnelltest aller 5 Kontrollfragen mit Debug-Output",
    )
    parser.add_argument(
        "--sequential", action="store_true", default=False,
        help="Fragen sequentiell statt parallel ausfuehren",
    )
    parser.add_argument(
        "--cell-size", type=int, default=2000,
        help="Grid-Zellgroesse in Metern (default: 2000)",
    )
    parser.add_argument(
        "--model", type=str, default=DEFAULT_MODEL,
        help=f"LLM-Modell (default: {DEFAULT_MODEL})",
    )
    args = parser.parse_args()

    run_all(
        cell_size=args.cell_size,
        sequential=args.sequential,
        model=args.model,
    )


if __name__ == "__main__":
    main()
