from __future__ import annotations

import duckdb
from pathlib import Path
import logging

CACHE_DUCKDB = Path("cache/duckdb")
CACHE_DUCKDB.mkdir(parents=True, exist_ok=True)
DB_PATH = CACHE_DUCKDB / "archaeology.duckdb"

DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
SITES_CSV = DATA_DIR / "sites_vec.csv"
FEATS_CSV = DATA_DIR / "feat_vec.csv"

log = logging.getLogger(__name__)

def export_csvs() -> tuple[Path, Path]:
    con = duckdb.connect(str(DB_PATH))

    con.execute(f"COPY (SELECT * FROM Sites) TO '{SITES_CSV}' (HEADER, DELIMITER ',')")
    con.execute(f"COPY (SELECT * FROM Features) TO '{FEATS_CSV}' (HEADER, DELIMITER ',')")

    con.close()
    log.info("Exported CSVs: %s, %s", SITES_CSV, FEATS_CSV)
    return SITES_CSV, FEATS_CSV
