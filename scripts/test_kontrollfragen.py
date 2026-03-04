#!/usr/bin/env python3
"""Test-Runner fuer die 5 Kontrollfragen des Wadi-Abu-Dom-Projekts.

Fuehrt jede Kontrollfrage durch den agentischen Loop und prueft ob das
Ergebnis den erwarteten Werten entspricht. Verwendet standardmaessig
Azure AI Foundry Deployments (Token-sparend), optional OpenAI direkt.

Ausfuehrung:
    python scripts/test_kontrollfragen.py
    python scripts/test_kontrollfragen.py --sequential
    python scripts/test_kontrollfragen.py --deployment gpt-4.1-global-dev-swedencentral
    python scripts/test_kontrollfragen.py --use-openai --model gpt-4.1
    python scripts/test_kontrollfragen.py --cell-size 5000
    python scripts/test_kontrollfragen.py --questions 1 4
    python scripts/test_kontrollfragen.py --neo4j-uri bolt://localhost:7687
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "app"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# Neo4j-URI Override VOR dem Import der Module setzen
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

from modules.helper import AgentResult, drain_llm_results
from modules.llm import run_agent
from modules.disambiguator import drain_disambiguation_results
from modules.logger import get_logger

logger = get_logger("debug")

_print_lock = threading.Lock()


def _tprint(*args, **kwargs) -> None:
    with _print_lock:
        print(*args, **kwargs, flush=True)


# ---------------------------------------------------------------------------
# Azure-Deployment-Konfiguration
# ---------------------------------------------------------------------------
def _load_azure_deployments() -> list[dict]:
    """Laedt Azure-Deployment-Konfigurationen aus der Umgebungsvariable."""
    raw = os.getenv("AZURE_AIF_DEPLOYMENTS", "")
    if not raw or raw.strip() == "":
        return []
    raw = raw.strip().strip("'\"")
    try:
        deployments = json.loads(raw)
        if isinstance(deployments, list) and deployments:
            return deployments
    except json.JSONDecodeError:
        pass
    return []


def _create_azure_client(deployment: dict) -> tuple[AzureOpenAI, str, dict]:
    """Erstellt einen AzureOpenAI-Client fuer ein Deployment."""
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
# Kontrollfragen mit erwarteten Ergebnissen
# ---------------------------------------------------------------------------
KONTROLLFRAGEN: list[dict] = [
    {
        "id": "Q1",
        "question": (
            "Gibt es eine statistisch signifikante Kolokation von ridge tumulus-sites "
            "(category: tumulus; location1: ridge) mit settlements (categories: habitation site; "
            "hut; settlement)? Wenn ja, weisen diese eine raeumliche Autokorrelation auf? "
            "Wo liegen die diesbezueglichen Hotspots? Korreliert bei ridge tumuli die Anzahl "
            "von Features pro Site mit ihrem Beitrag zur Kolokation mit habitation sites?"
        ),
        "expected": {
            "colocation_significant": False,
            "colocation_measurable": True,
            "hotspot_region": "32.05-32.37",
            "description": "Nicht signifikant (p > 0.05), aber messbar. Hotspot-Region: 32.05E-32.37E",
        },
        "checks": [
            ("p_value_above_005", "Kolokation p > 0.05 (nicht signifikant)"),
            ("moran_i_not_nan", "Moran's I ist ein gueltiger Wert"),
            ("hotspot_mentioned", "Hotspot-Region wird erwaehnt"),
            ("multi_part_answered", "Alle Teilfragen beantwortet"),
        ],
    },
    {
        "id": "Q2",
        "question": (
            "Gibt es eine signifikante raeumliche Autokorrelation von "
            "Sesshaftigkeitsindikatoren (categories: habitation site; hut; settlement) "
            "bzw. Mobilitaetsindikatoren (shelter; stoneplace; campsite; fireplace; gravel platform)?"
        ),
        "expected": {
            "sedentary_significant": True,
            "mobility_significant": True,
            "cluster_region": "31.55-32.05",
            "description": "Beide Gruppen signifikant. Cluster bei 31.55E-32.05E",
        },
        "checks": [
            ("both_groups_analyzed", "Beide Gruppen separat analysiert"),
            ("both_significant", "Beide Gruppen signifikant (p < 0.05)"),
            ("cluster_region_mentioned", "Cluster-Region wird erwaehnt"),
        ],
    },
    {
        "id": "Q3",
        "question": (
            "Gibt es statistisch signifikante Kolokationen von Sesshaftigkeits- bzw. "
            "Mobilitaetsindikatoren mit der Groesse (Anzahl von Features) von Friedhoefen "
            "(categories: box grave; cleft burial; grave; dome grave; tumulus) "
            "und deren Abstand zu einander?"
        ),
        "expected": {
            "size_sedentary_significant": True,
            "size_sedentary_negative": True,
            "distance_sedentary_significant": True,
            "distance_sedentary_negative": True,
            "mobility_not_significant": True,
            "description": (
                "Groesse vs. Sesshaftigkeit: signifikant, negativ. "
                "Abstand vs. Sesshaftigkeit: signifikant, negativ. "
                "Mobilitaet: nicht signifikant"
            ),
        },
        "checks": [
            ("size_analysis_present", "Groessen-Analyse durchgefuehrt"),
            ("distance_analysis_present", "Abstands-Analyse durchgefuehrt"),
            ("sedentary_mobility_separated", "Sesshaftigkeit und Mobilitaet getrennt"),
        ],
    },
    {
        "id": "Q4",
        "question": (
            "Gibt es eine statistisch signifikante Kolokation zwischen "
            "Sesshaftigkeitsindikatoren (categories: habitation site; hut; settlement) "
            "und Brunnen (category: well)?"
        ),
        "expected": {
            "significant": True,
            "positive": True,
            "description": "Deutlich signifikant. Positive Kolokation",
        },
        "checks": [
            ("significant_result", "Kolokation signifikant (p < 0.05)"),
            ("positive_colocation", "Positiver Moran's I"),
            ("moran_i_not_nan", "Moran's I ist ein gueltiger Wert"),
        ],
    },
    {
        "id": "Q5",
        "question": (
            "Gibt es eine statistisch signifikante Kolokation zwischen Mobilitaets- "
            "bzw. Sesshaftigkeitsindikatoren (categories: habitation site; hut; settlement) "
            "und der Feature-Category 'rock art'?"
        ),
        "expected": {
            "sedentary_significant": True,
            "mobility_not_significant": True,
            "description": "Sesshaftigkeit: signifikant. Mobilitaet: nicht signifikant",
        },
        "checks": [
            ("both_groups_analyzed", "Beide Gruppen separat analysiert"),
            ("sedentary_significant", "Sesshaftigkeit signifikant"),
            ("mobility_not_significant", "Mobilitaet nicht signifikant"),
        ],
    },
]


# ---------------------------------------------------------------------------
# Ergebnis-Extraktion
# ---------------------------------------------------------------------------
def _extract_values_from_stdout(stdout: str) -> list[dict]:
    """Extrahiert alle Moran's I / p_value Paare aus stdout."""
    results = []
    lines = stdout.split("\n")
    for line in lines:
        entry: dict = {}
        m_i = re.search(
            r"Moran(?:'s)?\s*(?:BV\s*)?I\s*[:=]\s*(-?[\d.]+(?:e[+-]?\d+)?)",
            line, re.IGNORECASE,
        )
        if m_i:
            try:
                entry["moran_I"] = float(m_i.group(1))
            except ValueError:
                pass
        m_p = re.search(
            r"(?:p[_-]?(?:value|sim|wert)|p)\s*[:=]\s*([\d.]+(?:e[+-]?\d+)?)",
            line, re.IGNORECASE,
        )
        if m_p:
            try:
                entry["p_value"] = float(m_p.group(1))
            except ValueError:
                pass
        m_n = re.search(r"n_units\s*[:=]\s*(\d+)", line, re.IGNORECASE)
        if m_n:
            entry["n_units"] = int(m_n.group(1))
        if entry:
            # Label aus der gleichen Zeile extrahieren
            label_match = re.match(r"^([^:=]+?):", line)
            if label_match:
                entry["label"] = label_match.group(1).strip()
            results.append(entry)
    return results


def _extract_from_agent(result: AgentResult) -> dict:
    """Extrahiert strukturierte Ergebnisse aus einem AgentResult."""
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    cypher_queries: list[str] = []
    python_codes: list[str] = []
    summary_jsons: list[dict] = []
    geojson_paths: list[str] = []
    all_success = True

    for tc in result.tool_calls:
        if tc.stdout:
            stdout_parts.append(tc.stdout)
        if tc.stderr:
            stderr_parts.append(tc.stderr)
        if tc.cypher_query:
            cypher_queries.append(tc.cypher_query)
        if tc.python_code:
            python_codes.append(tc.python_code)
        if tc.summary_json:
            summary_jsons.append(tc.summary_json)
        if tc.geojson_path:
            geojson_paths.append(tc.geojson_path)
        if not tc.success:
            all_success = False

    stdout = "\n".join(stdout_parts)
    stderr = "\n".join(stderr_parts)
    extracted_values = _extract_values_from_stdout(stdout)

    return {
        "answer": result.answer,
        "success": all_success,
        "stdout": stdout,
        "stderr": stderr,
        "cypher_queries": cypher_queries,
        "python_codes": python_codes,
        "summary_jsons": summary_jsons,
        "geojson_paths": geojson_paths,
        "extracted_values": extracted_values,
        "n_tool_calls": len(result.tool_calls),
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "cost_usd": result.cost_usd,
        "duration_seconds": result.duration_seconds,
        "model": result.model,
    }


# ---------------------------------------------------------------------------
# Ergebnis-Validierung
# ---------------------------------------------------------------------------
def _validate_result(q_id: str, extracted: dict) -> list[dict]:
    """Validiert die extrahierten Ergebnisse gegen die erwarteten Werte."""
    checks: list[dict] = []
    answer = (extracted.get("answer") or "").lower()
    stdout = (extracted.get("stdout") or "").lower()
    combined = answer + " " + stdout
    values = extracted.get("extracted_values", [])

    # Allgemeine Checks
    checks.append({
        "check": "execution_success",
        "passed": extracted.get("success", False),
        "detail": "Alle Tool-Aufrufe erfolgreich",
    })
    checks.append({
        "check": "has_answer",
        "passed": bool(extracted.get("answer", "").strip()),
        "detail": "Finale Antwort vorhanden",
    })

    # Fragen-spezifische Checks
    if q_id == "Q1":
        checks.extend(_validate_q1(combined, values, extracted))
    elif q_id == "Q2":
        checks.extend(_validate_q2(combined, values, extracted))
    elif q_id == "Q3":
        checks.extend(_validate_q3(combined, values, extracted))
    elif q_id == "Q4":
        checks.extend(_validate_q4(combined, values, extracted))
    elif q_id == "Q5":
        checks.extend(_validate_q5(combined, values, extracted))

    return checks


def _has_p_above(values: list[dict], threshold: float = 0.05) -> bool:
    """Prueft ob mindestens ein p-Wert ueber dem Schwellenwert liegt."""
    return any(v.get("p_value", 0) > threshold for v in values if "p_value" in v)


def _has_p_below(values: list[dict], threshold: float = 0.05) -> bool:
    """Prueft ob mindestens ein p-Wert unter dem Schwellenwert liegt."""
    return any(v.get("p_value", 1) < threshold for v in values if "p_value" in v)


def _has_valid_moran(values: list[dict]) -> bool:
    """Prueft ob mindestens ein gueltiger Moran's I Wert vorhanden ist."""
    import math
    return any(
        "moran_I" in v and v["moran_I"] is not None and not math.isnan(v["moran_I"])
        for v in values
    )


def _validate_q1(combined: str, values: list[dict], extracted: dict) -> list[dict]:
    checks = []
    checks.append({
        "check": "moran_i_not_nan",
        "passed": _has_valid_moran(values),
        "detail": "Moran's I ist ein gueltiger Wert (kein NaN)",
    })
    checks.append({
        "check": "p_value_above_005",
        "passed": _has_p_above(values, 0.05),
        "detail": "Kolokation p > 0.05 (nicht signifikant)",
    })
    checks.append({
        "check": "hotspot_mentioned",
        "passed": bool(re.search(r"32[.,]\d+", combined)),
        "detail": "Hotspot-Region mit Laengengrad erwaehnt",
    })
    # Multi-part: check all sub-questions addressed
    has_kolokation = any(k in combined for k in ["kolokation", "moran_bv", "bivariate"])
    has_autokorrelation = "autokorrelation" in combined or "moran's i" in combined
    has_hotspot = "hotspot" in combined or "cluster" in combined
    has_korrelation = "korrel" in combined or "pearson" in combined or "spearman" in combined
    checks.append({
        "check": "multi_part_answered",
        "passed": sum([has_kolokation, has_autokorrelation, has_hotspot, has_korrelation]) >= 3,
        "detail": f"Teilfragen beantwortet: Kol={has_kolokation}, Auto={has_autokorrelation}, "
                  f"Hot={has_hotspot}, Korr={has_korrelation}",
    })
    return checks


def _validate_q2(combined: str, values: list[dict], extracted: dict) -> list[dict]:
    checks = []
    # Both groups should be analyzed
    has_two_groups = (
        ("sesshaft" in combined and "mobilit" in combined)
        or ("gruppe a" in combined and "gruppe b" in combined)
        or ("sedentary" in combined and "mobility" in combined)
        or ("n_group_a" in combined and "n_group_b" in combined)
    )
    checks.append({
        "check": "both_groups_analyzed",
        "passed": has_two_groups,
        "detail": "Beide Gruppen (Sesshaftigkeit + Mobilitaet) separat analysiert",
    })
    checks.append({
        "check": "both_significant",
        "passed": len([v for v in values if v.get("p_value", 1) < 0.05]) >= 2,
        "detail": "Mindestens 2 signifikante Ergebnisse (p < 0.05)",
    })
    checks.append({
        "check": "cluster_region_mentioned",
        "passed": bool(re.search(r"3[12][.,]\d+", combined)),
        "detail": "Cluster-Region mit Laengengrad erwaehnt",
    })
    return checks


def _validate_q3(combined: str, values: list[dict], extracted: dict) -> list[dict]:
    checks = []
    checks.append({
        "check": "size_analysis_present",
        "passed": "groesse" in combined or "size" in combined or "n_cemetery" in combined,
        "detail": "Groessen-Analyse (Ansatz B) durchgefuehrt",
    })
    checks.append({
        "check": "distance_analysis_present",
        "passed": "abstand" in combined or "distance" in combined or "nn_dist" in combined,
        "detail": "Abstands-Analyse (Ansatz C) durchgefuehrt",
    })
    checks.append({
        "check": "sedentary_mobility_separated",
        "passed": (
            ("sesshaft" in combined and "mobilit" in combined)
            or ("sedentary" in combined and "mobility" in combined)
        ),
        "detail": "Sesshaftigkeit und Mobilitaet getrennt analysiert",
    })
    return checks


def _validate_q4(combined: str, values: list[dict], extracted: dict) -> list[dict]:
    checks = []
    checks.append({
        "check": "moran_i_not_nan",
        "passed": _has_valid_moran(values),
        "detail": "Moran's I ist ein gueltiger Wert",
    })
    checks.append({
        "check": "significant_result",
        "passed": _has_p_below(values, 0.05),
        "detail": "Kolokation signifikant (p < 0.05)",
    })
    positive_i = any(v.get("moran_I", 0) > 0 for v in values if "moran_I" in v)
    checks.append({
        "check": "positive_colocation",
        "passed": positive_i,
        "detail": "Positiver Moran's I (Kolokation, nicht Anti-Kolokation)",
    })
    return checks


def _validate_q5(combined: str, values: list[dict], extracted: dict) -> list[dict]:
    checks = []
    has_two_groups = (
        ("sesshaft" in combined and "mobilit" in combined)
        or ("sedentary" in combined and "mobility" in combined)
    )
    checks.append({
        "check": "both_groups_analyzed",
        "passed": has_two_groups,
        "detail": "Beide Gruppen separat analysiert",
    })
    # Look for one significant and one not significant
    has_sig = any("signifikant" in combined and "nicht" not in combined[:combined.index("signifikant")+20]
                  for _ in [1] if "signifikant" in combined)
    has_not_sig = "nicht signifikant" in combined
    checks.append({
        "check": "sedentary_significant",
        "passed": has_sig or _has_p_below(values, 0.05),
        "detail": "Sesshaftigkeit als signifikant erkannt",
    })
    checks.append({
        "check": "mobility_not_significant",
        "passed": has_not_sig or _has_p_above(values, 0.05),
        "detail": "Mobilitaet als nicht signifikant erkannt",
    })
    return checks


# ---------------------------------------------------------------------------
# Test-Ausfuehrung
# ---------------------------------------------------------------------------
def run_single_question(
    q: dict,
    model: str,
    cell_size: int,
    result_dir: Path,
    client=None,
    temperature: float | None = None,
    deployment_name: str = "",
) -> dict:
    """Fuehrt eine einzelne Kontrollfrage aus und validiert das Ergebnis."""
    q_id = q["id"]
    label = f"{q_id} ({deployment_name})" if deployment_name else q_id
    _tprint(f"\n{'='*60}")
    _tprint(f"  {label}: {q['question'][:80]}...")
    _tprint(f"{'='*60}")

    drain_llm_results()
    drain_disambiguation_results()

    data_path = str(result_dir / f"input_{q_id}.json")
    start = time.time()

    try:
        result = run_agent(
            question=q["question"],
            model=model,
            data_path=data_path,
            cell_size=cell_size,
            client=client,
            temperature=temperature,
        )
        extracted = _extract_from_agent(result)
    except Exception as exc:
        _tprint(f"  {label}: FEHLER -- {exc}")
        extracted = {
            "answer": "",
            "success": False,
            "stdout": "",
            "stderr": str(exc),
            "cypher_queries": [],
            "python_codes": [],
            "summary_jsons": [],
            "geojson_paths": [],
            "extracted_values": [],
            "n_tool_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0,
            "duration_seconds": round(time.time() - start, 2),
            "model": model,
        }

    # Validieren
    checks = _validate_result(q_id, extracted)
    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    score = passed / total if total > 0 else 0

    # Konsolenausgabe
    _tprint(f"\n  {label} Ergebnis: {passed}/{total} Checks bestanden ({score:.0%})")
    for c in checks:
        status = "PASS" if c["passed"] else "FAIL"
        _tprint(f"    [{status}] {c['detail']}")
    _tprint(f"  Tokens: {extracted['total_tokens']:,} | "
            f"Kosten: ${extracted['cost_usd']:.4f} | "
            f"Dauer: {extracted['duration_seconds']:.1f}s")

    return {
        "id": q_id,
        "question": q["question"],
        "expected": q["expected"],
        "checks": checks,
        "passed": passed,
        "total": total,
        "score": score,
        "answer": extracted.get("answer", ""),
        "stdout": extracted.get("stdout", ""),
        "stderr": extracted.get("stderr", ""),
        "cypher_queries": extracted.get("cypher_queries", []),
        "python_codes": extracted.get("python_codes", []),
        "summary_jsons": extracted.get("summary_jsons", []),
        "extracted_values": extracted.get("extracted_values", []),
        "n_tool_calls": extracted.get("n_tool_calls", 0),
        "prompt_tokens": extracted.get("prompt_tokens", 0),
        "completion_tokens": extracted.get("completion_tokens", 0),
        "total_tokens": extracted.get("total_tokens", 0),
        "cost_usd": extracted.get("cost_usd", 0),
        "duration_seconds": extracted.get("duration_seconds", 0),
        "model": model,
    }


def run_all(
    cell_size: int = 2000,
    sequential: bool = False,
    questions: list[int] | None = None,
    use_openai: bool = False,
    model: str | None = None,
    deployment_filter: str | None = None,
) -> dict:
    """Fuehrt alle Kontrollfragen aus und gibt den Gesamtbericht zurueck.

    Verwendet standardmaessig Azure AI Foundry Deployments (Round-Robin).
    Mit --use-openai wird stattdessen die OpenAI API direkt verwendet.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    result_dir = Path("results") / "kontrollfragen" / ts
    result_dir.mkdir(parents=True, exist_ok=True)

    # Fragen filtern
    if questions:
        selected = [q for q in KONTROLLFRAGEN if int(q["id"][1:]) in questions]
    else:
        selected = KONTROLLFRAGEN

    # Azure oder OpenAI?
    azure_deployments: list[dict] = []
    if not use_openai:
        azure_deployments = _load_azure_deployments()
        if deployment_filter:
            azure_deployments = [d for d in azure_deployments if d["name"] == deployment_filter]
        if not azure_deployments:
            _tprint("WARNUNG: Keine Azure-Deployments gefunden. Fallback auf OpenAI API.")
            use_openai = True

    effective_model = model or "gpt-4.1"

    print(f"\nKontrollfragen-Test")
    if use_openai:
        print(f"  Backend:    OpenAI API ({effective_model})")
    else:
        print(f"  Backend:    Azure AI Foundry ({len(azure_deployments)} Deployments)")
        for d in azure_deployments:
            print(f"              - {d['name']} ({d['deployment_id']})")
    print(f"  Zellgroesse: {cell_size}m")
    print(f"  Fragen:     {len(selected)}")
    print(f"  Modus:      {'sequentiell' if sequential else 'parallel'}")
    print(f"  Ausgabe:    {result_dir}")

    results: list[dict] = []

    def _make_kwargs(idx: int) -> dict:
        """Baut die Keyword-Argumente fuer run_single_question."""
        if use_openai:
            return {
                "model": effective_model,
                "client": None,
                "temperature": None,
                "deployment_name": "",
            }
        dep = azure_deployments[idx % len(azure_deployments)]
        client, deployment_id, params = _create_azure_client(dep)
        return {
            "model": deployment_id,
            "client": client,
            "temperature": params.get("temperature", 0.4),
            "deployment_name": dep["name"],
        }

    if sequential:
        for i, q in enumerate(selected):
            kw = _make_kwargs(i)
            r = run_single_question(q, kw["model"], cell_size, result_dir,
                                     client=kw["client"], temperature=kw["temperature"],
                                     deployment_name=kw["deployment_name"])
            results.append(r)
    else:
        max_workers = min(len(selected), len(azure_deployments)) if azure_deployments else len(selected)
        with ThreadPoolExecutor(max_workers=max(max_workers, 1)) as executor:
            futures = {}
            for i, q in enumerate(selected):
                kw = _make_kwargs(i)
                fut = executor.submit(
                    run_single_question, q, kw["model"], cell_size, result_dir,
                    client=kw["client"], temperature=kw["temperature"],
                    deployment_name=kw["deployment_name"],
                )
                futures[fut] = q
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as exc:
                    q = futures[fut]
                    _tprint(f"  {q['id']}: KRITISCHER FEHLER -- {exc}")
                    logger.exception("Kontrollfrage fehlgeschlagen: %s", exc)

    # Sortieren nach Q-Nummer
    results.sort(key=lambda r: r["id"])

    # Gesamtstatistik
    total_passed = sum(r["passed"] for r in results)
    total_checks = sum(r["total"] for r in results)
    overall_score = total_passed / total_checks if total_checks > 0 else 0
    total_cost = sum(r["cost_usd"] for r in results)
    total_duration = sum(r["duration_seconds"] for r in results)
    total_tokens = sum(r["total_tokens"] for r in results)

    backend = "openai" if use_openai else "azure"
    report = {
        "timestamp": ts,
        "backend": backend,
        "model": effective_model if use_openai else None,
        "azure_deployments": [d["name"] for d in azure_deployments] if azure_deployments else None,
        "cell_size": cell_size,
        "n_questions": len(results),
        "total_passed": total_passed,
        "total_checks": total_checks,
        "overall_score": round(overall_score, 4),
        "total_cost_usd": round(total_cost, 6),
        "total_duration_seconds": round(total_duration, 2),
        "total_tokens": total_tokens,
        "results": results,
    }

    # Speichern
    report_path = result_dir / "kontrollfragen.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False, default=str)

    # Zusammenfassung
    print(f"\n{'='*60}")
    print(f"ZUSAMMENFASSUNG")
    print(f"{'='*60}")
    for r in results:
        status = "PASS" if r["score"] >= 0.8 else "TEIL" if r["score"] >= 0.5 else "FAIL"
        print(f"  {r['id']}: [{status}] {r['passed']}/{r['total']} ({r['score']:.0%}) "
              f"| ${r['cost_usd']:.4f} | {r['duration_seconds']:.1f}s")
    print(f"\n  Gesamt: {total_passed}/{total_checks} ({overall_score:.0%})")
    print(f"  Kosten: ${total_cost:.4f} | Tokens: {total_tokens:,} | Dauer: {total_duration:.1f}s")
    print(f"  Bericht: {report_path}")
    print(f"{'='*60}")

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test-Runner fuer die 5 Kontrollfragen (Azure AI Foundry als Default)",
    )
    parser.add_argument(
        "--use-openai", action="store_true", default=False,
        help="OpenAI API direkt verwenden statt Azure",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Modellname fuer OpenAI-Modus (default: gpt-4.1)",
    )
    parser.add_argument(
        "--deployment", type=str, default=None,
        help="Nur ein bestimmtes Azure-Deployment verwenden (Name)",
    )
    parser.add_argument(
        "--cell-size", type=int, default=2000,
        help="Grid-Zellgroesse in Metern (default: 2000)",
    )
    parser.add_argument(
        "--sequential", action="store_true", default=False,
        help="Fragen sequentiell statt parallel ausfuehren",
    )
    parser.add_argument(
        "--questions", type=int, nargs="*", default=None,
        help="Nur bestimmte Fragen ausfuehren (z.B. --questions 1 3 5)",
    )
    parser.add_argument(
        "--neo4j-uri", type=str, default=None,
        help="Neo4j-URI (default: aus .env)",
    )
    args = parser.parse_args()

    report = run_all(
        cell_size=args.cell_size,
        sequential=args.sequential,
        questions=args.questions,
        use_openai=args.use_openai,
        model=args.model,
        deployment_filter=args.deployment,
    )

    # Exit-Code basierend auf Ergebnis
    if report["overall_score"] >= 0.9:
        sys.exit(0)
    elif report["overall_score"] >= 0.7:
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
