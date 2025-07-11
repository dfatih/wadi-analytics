from __future__ import annotations

import os
import json
import hashlib
import pickle
import logging
import time
from pathlib import Path
from typing import List

import duckdb
import pandas as pd
import openai
import streamlit as st

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CACHE_DUCKDB = Path("cache/duckdb")
CACHE_DUCKDB.mkdir(parents=True, exist_ok=True)
DB_PATH = CACHE_DUCKDB / "archaeology.duckdb"
EMBED_CACHE_PATH = CACHE_DUCKDB / "embeddings.duckdb"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBED_MODEL = "text-embedding-3-small"
SITE_TEXT_COLS = ["Category", "Location1", "Location2", "Surface"]
FEAT_TEXT_COLS = ["Category", "Location1", "Location2", "Condition", "Age", "Category2",
                  "RockArt1", "RockArt2", "RockArt3", "RockArt4", "RockArt5", "RockArt6"]

log = logging.getLogger(__name__)
openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _concat_text(row: pd.Series, cols: list[str]) -> str:
    return " | ".join(str(row[c]) for c in cols if pd.notna(row.get(c)))

def _make_cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _load_embedding_cache(con: duckdb.DuckDBPyConnection) -> dict[str, list[float]]:
    con.execute("CREATE TABLE IF NOT EXISTS emb_cache (key TEXT PRIMARY KEY, vec BLOB)")
    rows = con.execute("SELECT key, vec FROM emb_cache").fetchall()
    return {k: pickle.loads(v) for k, v in rows}

def _store_embedding(con: duckdb.DuckDBPyConnection, key: str, vec: list[float]) -> None:
    con.execute("INSERT OR REPLACE INTO emb_cache VALUES (?, ?)", [key, pickle.dumps(vec)])

def _get_openai_embedding(text: str) -> list[float]:
    res = openai_client.embeddings.create(input=[text], model=EMBED_MODEL)
    return res.data[0].embedding

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def generate_embeddings() -> None:
    con = duckdb.connect(str(DB_PATH))
    cache_con = duckdb.connect(str(EMBED_CACHE_PATH))
    cache = _load_embedding_cache(cache_con)

    for table, cols in [("Sites", SITE_TEXT_COLS), ("Features", FEAT_TEXT_COLS)]:
        st.write(f"### Embedding table: `{table}`")
        df = con.execute(f"SELECT * FROM {table}").fetchdf()

        if "embedding" not in df.columns:
            df["embedding"] = None

        updated = 0
        total = len(df)
        bar = st.progress(0)
        status = st.empty()
        stats = st.empty()
        start_time = time.time()

        for idx, row in df.iterrows():
            if pd.notna(row.get("embedding")):
                continue
            text = _concat_text(row, cols)
            key = _make_cache_key(text)
            vec = cache.get(key)
            if vec is None:
                vec = _get_openai_embedding(text)
                _store_embedding(cache_con, key, vec)
                cache[key] = vec
            df.at[idx, "embedding"] = vec
            updated += 1

            pct = int((idx + 1) / total * 100)
            elapsed = time.time() - start_time
            eta = (elapsed / (idx + 1)) * (total - idx - 1)
            status.text(f"{table}: Row {idx + 1} / {total} — {pct}% — ETA: {int(eta)}s")
            bar.progress(pct)

        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM df")
        log.info("%s: added %d new embeddings", table, updated)
        stats.success(f"{table}: {updated} embeddings added out of {total} rows.")

    con.close()
    cache_con.close()

if __name__ == "__main__":
    generate_embeddings()
