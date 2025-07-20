#!/usr/bin/env python3
"""
rebuild_concepts_standalone.py
──────────────────────────────
Standalone‑Updater für concepts.yml ohne Abhängigkeit vom internen
`modules.neo4j`‑Package.  Nutzt die Neo4j‑HTTP‑API.

Aufruf (PowerShell / CMD):
    python rebuild_concepts_standalone.py --mode write
    python rebuild_concepts_standalone.py --mode update
"""

import argparse, base64, json, os, sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import requests
import yaml


# ---------------------------------------------------------------------------
# Konfiguration & CLI
# ---------------------------------------------------------------------------
def _default_paths(repo_root: Path):
    return (
        repo_root / "extract_concepts_for_yaml__nan_safe.cypher",
        repo_root / "concepts.yml",
    )


def parse_cli() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parent
    cypher_def, yaml_def = _default_paths(repo_root)

    p = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--cypher", type=Path, default=cypher_def,
                   help="Pfad zum Cypher‑Skript")
    p.add_argument("--yaml",   type=Path, default=yaml_def,
                   help="Ziel‑YAML (wird erstellt oder aktualisiert)")
    p.add_argument("--mode",   choices=["write", "update"], default="write",
                   help="write = Datei neu schreiben, update = bestehende ergänzen")
    p.add_argument("--uri",    default=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                   help="Bolt‑ oder HTTP‑URI deiner Neo4j‑Instanz")
    p.add_argument("--user",   default=os.getenv("NEO4J_USER", "neo4j"),
                   help="Neo4j‑Benutzername")
    p.add_argument("--password", default=os.getenv("NEO4J_PASSWORD", "password"),
                   help="Neo4j‑Passwort")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Cypher via HTTP REST
# ---------------------------------------------------------------------------
def _bolt_to_http(uri: str) -> str:
    """
    Wandelt bolt://host:7687 → http://host:7474  (oder https) um.
    """
    import re
    prot, rest = uri.split("://", 1)
    host_port  = rest.rstrip("/").split("/")[0]
    if ":" in host_port:
        host, port = host_port.split(":")
        port = "7474" if port == "7687" else port
    else:
        host, port = host_port, "7474"
    scheme = "https" if prot.endswith("+s") else "http"
    return f"{scheme}://{host}:{port}/db/neo4j/tx/commit"


def run_cypher_http(uri: str, user: str, pwd: str, cypher: str) -> List[Dict]:
    endpoint = _bolt_to_http(uri)
    payload  = {"statements": [{"statement": cypher}]}
    resp = requests.post(
        endpoint,
        json=payload,
        headers={"Content-Type": "application/json"},
        auth=(user, pwd),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        msg = data["errors"][0].get("message", data["errors"])
        raise RuntimeError(f"Cypher‑Fehler: {msg}")

    cols  = data["results"][0]["columns"]
    rows  = data["results"][0]["data"]
    return [dict(zip(cols, r["row"])) for r in rows]


# ---------------------------------------------------------------------------
# YAML-Erzeugung & Merge
# ---------------------------------------------------------------------------
def build_structure(rows: List[Dict]) -> Dict:
    """
    rows: [{key, source_table, value, occurrences}, …]  →  geschachtelte Dict‑Struktur
    """
    # sortiert, damit Häufigkeit absteigend erscheint
    rows = sorted(rows, key=lambda r: (r["key"], r["source_table"], -r["occurrences"]))
    struct: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))

    for r in rows:
        val = str(r["value"]).strip().lower()
        if not val or val == "nan":
            continue
        sec, tab = r["key"], r["source_table"]
        if val not in struct[sec][tab]:
            struct[sec][tab].append(val)
    return {k: dict(v) for k, v in struct.items()}


def merge_update(old: Dict, new: Dict) -> Dict:
    for sec, tabs in new.items():
        if sec not in old:
            old[sec] = tabs
            continue
        for tab, vals in tabs.items():
            old.setdefault(sec, {}).setdefault(tab, [])
            for v in vals:
                if v not in old[sec][tab]:
                    old[sec][tab].append(v)
    return old


def dump_yaml(data: Dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            data,
            fh,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            indent=2,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    args = parse_cli()

    if not args.cypher.exists():
        sys.exit(f"[ERROR] Cypher‑Datei nicht gefunden: {args.cypher}")

    cypher_txt = args.cypher.read_text(encoding="utf-8")
    print(f"[INFO] Führe Cypher‑Skript aus → {args.cypher}")

    try:
        rows = run_cypher_http(args.uri, args.user, args.password, cypher_txt)
    except Exception as exc:
        sys.exit(f"[ERROR] Neo4j‑Aufruf fehlgeschlagen: {exc}")

    if not rows:
        sys.exit("[ERROR] Cypher lieferte 0 Zeilen – Abbruch.")

    new_struct = build_structure(rows)

    if args.mode == "write" or not args.yaml.exists():
        dump_yaml(new_struct, args.yaml)
        print(f"[OK] Neue concepts.yml geschrieben → {args.yaml}")
    else:
        with args.yaml.open("r", encoding="utf-8") as fh:
            old_struct = yaml.safe_load(fh) or {}
        merged = merge_update(old_struct, new_struct)
        dump_yaml(merged, args.yaml)
        print(f"[OK] concepts.yml aktualisiert → {args.yaml}")


if __name__ == "__main__":
    main()
