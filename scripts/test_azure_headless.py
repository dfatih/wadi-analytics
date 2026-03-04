#!/usr/bin/env python3
"""Headless-Test aller 5 Kontrollfragen via Azure OpenAI -- parallel.

Verteilt die Fragen auf mehrere Azure-Deployments (Round-Robin) und
fuehrt sie parallel aus. Jede Frage bekommt ein eigenes AzureOpenAI-Client.

Liest AZURE_AIF_DEPLOYMENTS aus der .env-Datei oder Umgebungsvariable.

Ausfuehrung:
    python3 scripts/test_azure_headless.py
    python3 scripts/test_azure_headless.py --sequential
    python3 scripts/test_azure_headless.py --cell-size 5000
    python3 scripts/test_azure_headless.py --deployment gpt-4.1-global-dev-swedencentral
    python3 scripts/test_azure_headless.py --neo4j-uri bolt://localhost:7687
"""
from __future__ import annotations

import argparse
import json
import os
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

# Neo4j-URI Override VOR dem Import der Module setzen,
# da helper.py den Driver beim Import erstellt.
_neo4j_uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
for arg in sys.argv:
    if arg.startswith("--neo4j-uri="):
        _neo4j_uri = arg.split("=", 1)[1]
    elif arg == "--neo4j-uri":
        idx = sys.argv.index(arg)
        if idx + 1 < len(sys.argv):
            _neo4j_uri = sys.argv[idx + 1]
os.environ["NEO4J_URI"] = _neo4j_uri

from openai import AzureOpenAI

from modules.helper import drain_llm_results, AgentResult, ToolCallRecord
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
# Azure-Deployment-Konfiguration
# ---------------------------------------------------------------------------
def load_azure_deployments() -> list[dict]:
    """Laedt Azure-Deployment-Konfigurationen aus der Umgebungsvariable."""
    raw = os.getenv("AZURE_AIF_DEPLOYMENTS", "")
    if not raw or raw.strip() == "":
        raise RuntimeError(
            "AZURE_AIF_DEPLOYMENTS nicht gesetzt. "
            "Bitte in .env oder als Umgebungsvariable definieren."
        )
    # Die Variable kann mit einfachen Anfuehrungszeichen umschlossen sein
    raw = raw.strip().strip("'\"")
    deployments = json.loads(raw)
    if not isinstance(deployments, list) or not deployments:
        raise RuntimeError("AZURE_AIF_DEPLOYMENTS muss ein nicht-leeres JSON-Array sein.")
    return deployments


def create_azure_client(deployment: dict) -> tuple[AzureOpenAI, str, dict]:
    """Erstellt einen AzureOpenAI-Client fuer ein Deployment.

    Returns:
        (client, deployment_id, parameters)
    """
    client = AzureOpenAI(
        azure_endpoint=deployment["endpoint"],
        api_key=deployment["api_key"],
        api_version=deployment["api_version"],
    )
    return (
        client,
        deployment["deployment_id"],
        deployment.get("parameters", {}),
    )


# ---------------------------------------------------------------------------
# Kontrollfragen (identisch mit test_kontrollfragen.py)
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
            "campsite; fireplace; gravel platform)?"
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
            "von Friedhoefen (categories: box grave; cleft burial; grave; "
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
# Debug-Extraktion
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
    azure_client: AzureOpenAI,
    deployment_id: str,
    deployment_name: str,
    temperature: float = 0.4,
) -> dict:
    """Fuehrt eine Kontrollfrage mit einem Azure-Deployment aus."""
    qid = kf["id"]
    _tprint(f"\n  [{qid}] Starte auf {deployment_name}: {kf['label']}")

    drain_llm_results()
    drain_disambiguation_results()

    data_path = str(result_dir / f"input_{qid}.json")
    t0 = time.time()

    try:
        agent_result = run_agent(
            kf["question"],
            model=deployment_id,
            data_path=data_path,
            cell_size=cell_size,
            client=azure_client,
            temperature=temperature,
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

    record = {
        "id": qid,
        "label": kf["label"],
        "question": kf["question"],
        "expected": kf["expected"],
        "deployment": deployment_name,
        "deployment_id": deployment_id,
        "success": success,
        "error": error,
        "duration_seconds": round(elapsed, 2),
        "debug": debug,
    }

    if success:
        _tprint(
            f"  [{qid}] Fertig in {elapsed:.1f}s | "
            f"Deployment: {deployment_name} | "
            f"Tokens: {debug.get('total_tokens', 0)} | "
            f"Steps: {debug.get('n_steps', 0)}"
        )
        answer = debug.get("answer", "")
        if answer:
            _tprint(f"  [{qid}] Antwort: {answer[:200]}...")
    else:
        _tprint(f"  [{qid}] FEHLER auf {deployment_name}: {error}")

    return record


# ---------------------------------------------------------------------------
# Zusammenfassung
# ---------------------------------------------------------------------------
def _print_summary(results: list[dict]) -> None:
    """Druckt eine kompakte Zusammenfassung aller Ergebnisse."""
    print(f"\n{'='*70}")
    print("ZUSAMMENFASSUNG")
    print(f"{'='*70}")

    total_tokens = 0
    total_duration = 0.0

    for r in results:
        qid = r.get("id", "?")
        label = r.get("label", "")
        success = r.get("success", False)
        debug = r.get("debug", {})
        expected = r.get("expected", {})
        deployment = r.get("deployment", "")

        status = "OK" if success else "FEHLER"
        tokens = debug.get("total_tokens", 0) or 0
        duration = r.get("duration_seconds", 0) or 0

        total_tokens += tokens
        total_duration += duration

        print(f"\n  {qid}: {label}")
        print(f"    Status:     {status}")
        print(f"    Deployment: {deployment}")
        print(f"    Dauer:      {duration:.1f}s | Tokens: {tokens}")

        if success and debug:
            answer = debug.get("answer", "")
            if answer:
                print(f"    Antwort:    {answer[:300]}")

            for step in debug.get("steps", []):
                idx = step["step_index"]
                tool = step["tool_name"]
                ok = "OK" if step["success"] else "FAIL"
                print(f"    Step {idx}: {tool} [{ok}]")

                if step.get("cypher_query"):
                    cq = step["cypher_query"].replace("\n", " ")[:120]
                    print(f"      Cypher: {cq}...")
                if step.get("stdout"):
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

        print(f"    Erwartet:   {expected.get('notes', '')}")

    print(f"\n{'='*70}")
    print(f"Gesamt: {total_duration:.1f}s | {total_tokens} Tokens")
    print(f"{'='*70}")


# ---------------------------------------------------------------------------
# Hauptlogik
# ---------------------------------------------------------------------------
def run_all(
    cell_size: int = 2000,
    sequential: bool = False,
    deployment_filter: str | None = None,
) -> Path:
    """Fuehrt alle Kontrollfragen auf Azure-Deployments aus."""
    deployments = load_azure_deployments()

    # Optional filtern auf ein bestimmtes Deployment
    if deployment_filter:
        deployments = [d for d in deployments if d["name"] == deployment_filter]
        if not deployments:
            raise RuntimeError(f"Deployment '{deployment_filter}' nicht gefunden.")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    result_dir = Path("results") / "kontrollfragen_azure" / ts
    result_dir.mkdir(parents=True, exist_ok=True)

    print("Azure Kontrollfragen-Test (Headless)")
    print(f"  Deployments:  {len(deployments)}")
    print(f"  Cell-Size:    {cell_size}m")
    print(f"  Modus:        {'sequentiell' if sequential else 'parallel'}")
    print(f"  Ausgabe:      {result_dir}")
    print(f"  Fragen:       {len(KONTROLLFRAGEN)}")
    for d in deployments:
        print(f"    - {d['name']} ({d['deployment_id']})")
    print(f"{'='*70}")

    results: list[dict] = []

    if sequential:
        for i, kf in enumerate(KONTROLLFRAGEN):
            dep = deployments[i % len(deployments)]
            azure_client, deployment_id, params = create_azure_client(dep)
            record = _run_question(
                kf, cell_size, result_dir,
                azure_client=azure_client,
                deployment_id=deployment_id,
                deployment_name=dep["name"],
                temperature=params.get("temperature", 0.4),
            )
            results.append(record)
    else:
        with ThreadPoolExecutor(max_workers=min(len(KONTROLLFRAGEN), len(deployments))) as executor:
            futures = {}
            for i, kf in enumerate(KONTROLLFRAGEN):
                dep = deployments[i % len(deployments)]
                azure_client, deployment_id, params = create_azure_client(dep)
                fut = executor.submit(
                    _run_question,
                    kf, cell_size, result_dir,
                    azure_client=azure_client,
                    deployment_id=deployment_id,
                    deployment_name=dep["name"],
                    temperature=params.get("temperature", 0.4),
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

    _print_summary(results)

    # Speichern
    output = {
        "timestamp": ts,
        "deployments": [d["name"] for d in deployments],
        "cell_size": cell_size,
        "results": results,
    }
    output_path = result_dir / "kontrollfragen_azure.json"
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False, default=str)

    print(f"\nErgebnisse gespeichert: {output_path}")
    return result_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Headless-Test aller 5 Kontrollfragen via Azure OpenAI",
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
        "--deployment", type=str, default=None,
        help="Nur ein bestimmtes Deployment verwenden (Name aus AZURE_AIF_DEPLOYMENTS)",
    )
    parser.add_argument(
        "--neo4j-uri", type=str, default=None,
        help="Neo4j-URI (default: auto-detect, localhost statt Docker-Hostname)",
    )
    args = parser.parse_args()

    run_all(
        cell_size=args.cell_size,
        sequential=args.sequential,
        deployment_filter=args.deployment,
    )


if __name__ == "__main__":
    main()
