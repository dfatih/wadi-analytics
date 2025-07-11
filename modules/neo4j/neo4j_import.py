import json
from pathlib import Path
from typing import Any, Callable, Iterator, Union

import pandas as pd
from neo4j import GraphDatabase, basic_auth
from shapely import wkt

from modules.logger import get_logger

log = get_logger(__name__)
DEFAULT_BATCH_SIZE = 1000


def _create_constraints(tx) -> None:
    tx.run("CREATE CONSTRAINT IF NOT EXISTS FOR (s:Site) REQUIRE s.SiteID IS UNIQUE")
    tx.run("CREATE CONSTRAINT IF NOT EXISTS FOR (f:Feature) REQUIRE f.FeatureID IS UNIQUE")


def _read_csv_in_chunks(csv_path: Union[str, Path], chunk_size: int) -> Iterator[list[dict[str, Any]]]:
    path = Path(csv_path)
    for chunk in pd.read_csv(path, chunksize=chunk_size, dtype=str):
        records = chunk.to_dict(orient="records")
        for row in records:
            if "embedding" in row and isinstance(row["embedding"], str):
                try:
                    row["embedding"] = json.loads(row["embedding"])
                except json.JSONDecodeError:
                    row["embedding"] = None
        yield records


def _import_sites_batch(tx, batch: list[dict[str, Any]]) -> None:
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (s:Site {SiteID: row.SiteID})
        SET s.Category     = row.Category,
            s.Location1    = row.Location1,
            s.Location2    = row.Location2,
            s.Surface      = row.Surface,
            s.NoOfFeatures = toInteger(row.NoOfFeatures),
            s.X            = toFloat(row.X),
            s.Y            = toFloat(row.Y),
            s.Shape_Length = toFloat(row.Shape_Length),
            s.Shape_Area   = toFloat(row.Shape_Area),
            s.Lat          = toFloat(row.Lat),
            s.Lon          = toFloat(row.Lon),
            s.geometry     = row.geometry,
            s.embedding    = CASE WHEN row.embedding IS NOT NULL THEN row.embedding ELSE NULL END
        """,
        rows=batch,
    )


def _import_feats_batch(tx, batch: list[dict[str, Any]]) -> int:
    tx.run(
        """
        UNWIND $rows AS row
        OPTIONAL MATCH (s:Site {SiteID: row.Site})
        WITH row, s
        WHERE s IS NOT NULL
        MERGE (f:Feature {FeatureID: row.FeatureID})
        SET f.Category  = row.Category,
            f.Location1 = row.Location1,
            f.Location2 = row.Location2,
            f.Length    = toFloat(row.Length),
            f.Width     = toFloat(row.Width),
            f.Height    = toFloat(row.Height),
            f.Condition = row.Condition,
            f.Age       = toInteger(row.Age),
            f.X         = toFloat(row.X),
            f.Y         = toFloat(row.Y),
            f.Lat       = toFloat(row.Lat),
            f.Lon       = toFloat(row.Lon),
            f.Category2 = row.Category2,
            f.RockArt1  = row.RockArt1,
            f.RockArt2  = row.RockArt2,
            f.RockArt3  = row.RockArt3,
            f.RockArt4  = row.RockArt4,
            f.RockArt5  = row.RockArt5,
            f.RockArt6  = row.RockArt6,
            f.geometry  = row.geometry,
            f.embedding = CASE WHEN row.embedding IS NOT NULL THEN row.embedding ELSE NULL END
        MERGE (s)-[:HAS_FEATURE]->(f)
        MERGE (f)-[:LOCATED_ON]->(s)
        """,
        rows=batch,
    )
    return len(batch)


def import_to_neo4j(
    uri: str,
    user: str,
    password: str,
    sites_csv: Union[str, Path],
    feats_csv: Union[str, Path],
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_cb: Callable[[str, int, int], None] | None = None,
) -> None:
    sites_path = Path(sites_csv)
    feats_path = Path(feats_csv)

    if not sites_path.exists():
        raise FileNotFoundError(f"Sites CSV not found: {sites_path}")
    if not feats_path.exists():
        raise FileNotFoundError(f"Features CSV not found: {feats_path}")

    try:
        driver = GraphDatabase.driver(uri, auth=basic_auth(user, password))
    except Exception as exc:
        log.error("Could not connect to Neo4j at %s: %s", uri, exc)
        raise ConnectionError(f"Failed to connect to Neo4j: {exc}")

    total_sites = sum(1 for _ in pd.read_csv(sites_path, chunksize=batch_size))
    total_feats = sum(1 for _ in pd.read_csv(feats_path, chunksize=batch_size))

    processed_sites = 0
    processed_feats = 0

    try:
        with driver.session() as session:
            _create_constraints(session)

            for batch in _read_csv_in_chunks(sites_path, batch_size):
                session.execute_write(_import_sites_batch, batch)
                processed_sites += len(batch)
                if progress_cb:
                    progress_cb("sites", processed_sites, total_sites)
            log.info("Imported all Site rows: %s total.", processed_sites)

            for batch in _read_csv_in_chunks(feats_path, batch_size):
                parent_ids = {row["Site"] for row in batch if row.get("Site") is not None}
                existing = set()
                if parent_ids:
                    result = session.run(
                        "MATCH (s:Site) WHERE s.SiteID IN $ids RETURN collect(s.SiteID) AS existing",
                        ids=list(parent_ids),
                    )
                    existing = set(result.single()["existing"]) or set()

                valid_batch = [row for row in batch if row.get("Site") in existing]
                if valid_batch:
                    session.execute_write(_import_feats_batch, valid_batch)

                processed_feats += len(batch)
                if progress_cb:
                    progress_cb("feats", processed_feats, total_feats)
            log.info("Imported all Feature rows: %s total (orphans skipped).", processed_feats)

            # Generate :CLOSE_TO relationships between Sites and Features
            # session.run("""
            #     MATCH (a:Site)
            #     CALL {
            #         WITH a
            #         MATCH (b:Site)
            #         WHERE a.SiteID < b.SiteID
            #         AND point.distance(a.geometry, b.geometry) < 500
            #         RETURN b
            #     }
            #     MERGE (a)-[:CLOSE_TO_SITE]->(b)
            # """)


            # session.run("""
            #     MATCH (a:Feature)
            #     CALL {
            #         WITH a
            #         MATCH (b:Feature)
            #         WHERE a.FeatureID < b.FeatureID
            #         AND point.distance(a.geometry, b.geometry) < 500
            #         RETURN b
            #     }
            #     MERGE (a)-[:CLOSE_TO_FEATURE]->(b)
            # """)


    finally:
        driver.close()
        log.info("Neo4j driver closed.")