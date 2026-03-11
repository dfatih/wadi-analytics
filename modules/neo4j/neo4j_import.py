import json
from pathlib import Path
from typing import Any, Callable, Iterator, Union

import pandas as pd
from neo4j import GraphDatabase, basic_auth

from modules.logger import get_logger

log = get_logger(__name__)
DEFAULT_BATCH_SIZE = 1000
DEFAULT_PROXIMITY_METERS = 500


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------
def _create_constraints(session) -> None:
    """Uniqueness-Constraints fuer Site, Feature und RockArtMotif."""
    for stmt in [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Site) REQUIRE s.SiteID IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (f:Feature) REQUIRE f.FeatureID IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (m:RockArtMotif) REQUIRE m.name IS UNIQUE",
    ]:
        session.run(stmt)


# ---------------------------------------------------------------------------
# Indexe
# ---------------------------------------------------------------------------
def _create_indexes(session) -> None:
    """Legt Property-, Point-, Vektor- und Fulltext-Indexe an.

    Vektor-/Fulltext-Indexe werden einzeln mit Fehlerbehandlung ausgefuehrt,
    damit eine aeltere Neo4j-Version nicht den gesamten Import abbricht.
    """
    # Property- und Point-Indexe (funktionieren in allen Neo4j 5.x)
    safe_stmts = [
        "CREATE INDEX idx_feature_category IF NOT EXISTS FOR (f:Feature) ON (f.Category)",
        "CREATE INDEX idx_feature_location1 IF NOT EXISTS FOR (f:Feature) ON (f.Location1)",
        "CREATE INDEX idx_feature_age IF NOT EXISTS FOR (f:Feature) ON (f.Age)",
        "CREATE INDEX idx_site_category IF NOT EXISTS FOR (s:Site) ON (s.Category)",
        "CREATE INDEX idx_site_location1 IF NOT EXISTS FOR (s:Site) ON (s.Location1)",
        "CREATE POINT INDEX idx_site_location IF NOT EXISTS FOR (s:Site) ON (s.location)",
        "CREATE POINT INDEX idx_feature_location IF NOT EXISTS FOR (f:Feature) ON (f.location)",
    ]
    for stmt in safe_stmts:
        session.run(stmt)
    log.info("%d Standard-Indexe angelegt.", len(safe_stmts))

    # Optionale Indexe (Vektor + Fulltext) -- mit Fehlertoleranz
    optional_stmts = [
        ("Vektor (Site)", """
            CREATE VECTOR INDEX idx_site_embedding IF NOT EXISTS
            FOR (s:Site) ON (s.embedding)
            OPTIONS {indexConfig: {
              `vector.dimensions`: 1536,
              `vector.similarity_function`: 'cosine'
            }}
        """),
        ("Vektor (Feature)", """
            CREATE VECTOR INDEX idx_feature_embedding IF NOT EXISTS
            FOR (f:Feature) ON (f.embedding)
            OPTIONS {indexConfig: {
              `vector.dimensions`: 1536,
              `vector.similarity_function`: 'cosine'
            }}
        """),
        ("Fulltext (Feature)",
         "CREATE FULLTEXT INDEX idx_feature_text IF NOT EXISTS "
         "FOR (f:Feature) ON EACH [f.Category, f.Category2, f.Condition]"),
        ("Fulltext (Site)",
         "CREATE FULLTEXT INDEX idx_site_text IF NOT EXISTS "
         "FOR (s:Site) ON EACH [s.Category, s.Surface]"),
    ]
    for label, stmt in optional_stmts:
        try:
            session.run(stmt)
            log.info("Index '%s' angelegt.", label)
        except Exception as exc:
            log.warning("Index '%s' uebersprungen: %s", label, exc)


# ---------------------------------------------------------------------------
# Idempotenz: Aufraumen vor Re-Import
# ---------------------------------------------------------------------------
def _cleanup_before_import(session) -> None:
    """Raeumt migrierte Beziehungen/Knoten auf, damit ein Re-Import sauber laeuft."""
    for rel_type in ["HAS_ROCKART", "NEAR", "LOCATED_ON"]:
        total = 0
        while True:
            deleted = session.run(
                f"MATCH ()-[r:{rel_type}]->() WITH r LIMIT 50000 "
                f"DELETE r RETURN count(r) AS deleted"
            ).single()["deleted"]
            total += deleted
            if deleted == 0:
                break
        if total:
            log.info("Aufgeraeumt: %d %s-Beziehungen geloescht.", total, rel_type)

    deleted = session.run(
        "MATCH (m:RockArtMotif) DETACH DELETE m RETURN count(m) AS deleted"
    ).single()["deleted"]
    if deleted:
        log.info("Aufgeraeumt: %d RockArtMotif-Knoten geloescht.", deleted)


# ---------------------------------------------------------------------------
# Point-Properties
# ---------------------------------------------------------------------------
def _set_point_properties(session) -> None:
    """Erzeugt Neo4j-Point-Properties aus Lat/Lon fuer Spatial-Queries."""
    result_sites = session.run("""
        MATCH (s:Site)
        WHERE s.Lat IS NOT NULL AND s.Lon IS NOT NULL
        SET s.location = point({latitude: s.Lat, longitude: s.Lon})
        RETURN count(s) AS updated
    """).single()["updated"]
    log.info("Point-Property gesetzt: %d Sites.", result_sites)

    result_feats = session.run("""
        MATCH (f:Feature)
        WHERE f.Lat IS NOT NULL AND f.Lon IS NOT NULL
        SET f.location = point({latitude: f.Lat, longitude: f.Lon})
        RETURN count(f) AS updated
    """).single()["updated"]
    log.info("Point-Property gesetzt: %d Features.", result_feats)


# ---------------------------------------------------------------------------
# RockArt-Migration: Properties -> Knoten
# ---------------------------------------------------------------------------
def _migrate_rockart_to_nodes(session) -> None:
    """Liest RockArt1-6 von Feature-Knoten, erzeugt RockArtMotif-Knoten + HAS_ROCKART."""
    result = session.run("""
        MATCH (f:Feature)
        WITH f,
          [x IN [f.RockArt1, f.RockArt2, f.RockArt3,
                 f.RockArt4, f.RockArt5, f.RockArt6]
           WHERE x IS NOT NULL AND x IS :: STRING AND trim(x) <> ''] AS motive
        WHERE size(motive) > 0
        UNWIND range(0, size(motive)-1) AS i
        MERGE (m:RockArtMotif {name: motive[i]})
        MERGE (f)-[r:HAS_ROCKART]->(m)
        SET r.position = i + 1
        RETURN count(r) AS created
    """).single()["created"]
    log.info("HAS_ROCKART-Beziehungen erstellt: %d", result)


def _remove_rockart_properties(session) -> None:
    """Entfernt die flachen RockArt-Properties nach erfolgreicher Migration."""
    session.run("""
        MATCH (f:Feature)
        REMOVE f.RockArt1, f.RockArt2, f.RockArt3,
               f.RockArt4, f.RockArt5, f.RockArt6
    """)
    log.info("RockArt1-6 Properties von Feature-Knoten entfernt.")


# ---------------------------------------------------------------------------
# Proximity-Beziehungen (gleicher Typ)
# ---------------------------------------------------------------------------
def _create_proximity_relationships(
    session,
    distance_m: int = DEFAULT_PROXIMITY_METERS,
    progress_cb: Callable[[str, int, int], None] | None = None,
) -> None:
    """Berechnet CLOSE_TO_SITE und CLOSE_TO_FEATURE fuer benachbarte Knoten.

    Nutzt point.distance() auf der location-Property. Distanz wird als
    Property auf der Beziehung gespeichert.
    """
    if progress_cb:
        progress_cb("proximity", 0, 2)

    result_sites = session.run("""
        MATCH (a:Site), (b:Site)
        WHERE a.SiteID < b.SiteID
          AND a.location IS NOT NULL AND b.location IS NOT NULL
          AND point.distance(a.location, b.location) < $dist
        WITH a, b, point.distance(a.location, b.location) AS dist
        MERGE (a)-[r:CLOSE_TO_SITE]->(b)
        SET r.distance_m = dist
        RETURN count(r) AS created
    """, dist=distance_m).single()["created"]
    log.info("CLOSE_TO_SITE erstellt: %d (< %dm).", result_sites, distance_m)

    if progress_cb:
        progress_cb("proximity", 1, 2)

    result_feats = session.run("""
        MATCH (a:Feature), (b:Feature)
        WHERE a.FeatureID < b.FeatureID
          AND a.location IS NOT NULL AND b.location IS NOT NULL
          AND point.distance(a.location, b.location) < $dist
        WITH a, b, point.distance(a.location, b.location) AS dist
        MERGE (a)-[r:CLOSE_TO_FEATURE]->(b)
        SET r.distance_m = dist
        RETURN count(r) AS created
    """, dist=distance_m).single()["created"]
    log.info("CLOSE_TO_FEATURE erstellt: %d (< %dm).", result_feats, distance_m)

    if progress_cb:
        progress_cb("proximity", 2, 2)


# ---------------------------------------------------------------------------
# Cross-Proximity (Feature <-> Site)
# ---------------------------------------------------------------------------
def _create_cross_proximity(
    session,
    distance_m: int = DEFAULT_PROXIMITY_METERS,
    progress_cb: Callable[[str, int, int], None] | None = None,
) -> None:
    """NEAR-Beziehungen zwischen Features und nicht-Parent-Sites (< distance_m).

    Verwendet BoundingBox-Vorfilter ueber Lat/Lon um das kartesische Produkt
    zu vermeiden (~0.0045 Grad entspricht ca. 500m auf 18-19 N).
    """
    if progress_cb:
        progress_cb("cross_proximity", 0, 1)

    bbox_delta = distance_m / 111_000.0

    result = session.run("""
        MATCH (f:Feature)<-[:HAS_FEATURE]-(parent:Site)
        WHERE f.location IS NOT NULL
        WITH f, parent, f.Lat AS fLat, f.Lon AS fLon
        MATCH (s:Site)
        WHERE s.SiteID <> parent.SiteID
          AND s.location IS NOT NULL
          AND s.Lat > fLat - $delta AND s.Lat < fLat + $delta
          AND s.Lon > fLon - $delta AND s.Lon < fLon + $delta
          AND point.distance(f.location, s.location) < $dist
        WITH f, s, point.distance(f.location, s.location) AS d
        MERGE (f)-[r:NEAR]->(s)
        SET r.distance_m = d
        RETURN count(r) AS created
    """, dist=distance_m, delta=bbox_delta).single()["created"]
    log.info("NEAR-Beziehungen erstellt: %d (< %dm).", result, distance_m)

    if progress_cb:
        progress_cb("cross_proximity", 1, 1)


# ---------------------------------------------------------------------------
# CSV-Lesen
# ---------------------------------------------------------------------------
def _read_csv_in_chunks(
    csv_path: Union[str, Path], chunk_size: int
) -> Iterator[list[dict[str, Any]]]:
    """Liest CSV chunkweise und parst die Embedding-Spalte aus JSON."""
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


# ---------------------------------------------------------------------------
# Batch-Import: Sites
# ---------------------------------------------------------------------------
def _import_sites_batch(tx, batch: list[dict[str, Any]]) -> None:
    tx.run("""
        UNWIND $rows AS row
        MERGE (s:Site {SiteID: row.SiteID})
        SET s.Category     = row.Category,
            s.Location1    = row.Location1,
            s.Location2    = row.Location2,
            s.Surface      = row.Surface,
            s.NoOfFeatures = toInteger(row.NoOfFeatures),
            s.X            = toFloat(row.X),
            s.Y            = toFloat(row.Y),
            s.Shape_Length  = toFloat(row.Shape_Length),
            s.Shape_Area   = toFloat(row.Shape_Area),
            s.Lat          = toFloat(row.Lat),
            s.Lon          = toFloat(row.Lon),
            s.geometry     = row.geometry,
            s.embedding    = CASE WHEN row.embedding IS NOT NULL
                             THEN row.embedding ELSE NULL END
    """, rows=batch)


# ---------------------------------------------------------------------------
# Batch-Import: Features (RockArt1-6 werden temporaer mitgeladen fuer Migration)
# ---------------------------------------------------------------------------
def _import_feats_batch(tx, batch: list[dict[str, Any]]) -> int:
    tx.run("""
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
            f.embedding = CASE WHEN row.embedding IS NOT NULL
                         THEN row.embedding ELSE NULL END
        MERGE (s)-[:HAS_FEATURE]->(f)
    """, rows=batch)
    return len(batch)


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------
def import_to_neo4j(
    uri: str,
    user: str,
    password: str,
    sites_csv: Union[str, Path],
    feats_csv: Union[str, Path],
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_cb: Callable[[str, int, int], None] | None = None,
    create_proximity: bool = True,
) -> None:
    """Importiert Sites und Features aus CSV in Neo4j.

    Ablauf: Constraints -> Cleanup -> Sites -> Features -> RockArt-Migration
    -> Point-Properties -> Indexe -> Proximity -> Cross-Proximity.
    """
    sites_path = Path(sites_csv)
    feats_path = Path(feats_csv)

    if not sites_path.exists():
        raise FileNotFoundError(f"Sites-CSV nicht gefunden: {sites_path}")
    if not feats_path.exists():
        raise FileNotFoundError(f"Features-CSV nicht gefunden: {feats_path}")

    try:
        driver = GraphDatabase.driver(uri, auth=basic_auth(user, password))
    except Exception as exc:
        log.error("Neo4j-Verbindung fehlgeschlagen (%s): %s", uri, exc)
        raise ConnectionError(f"Neo4j-Verbindung fehlgeschlagen: {exc}")

    total_sites = sum(1 for _ in pd.read_csv(sites_path, chunksize=batch_size))
    total_feats = sum(1 for _ in pd.read_csv(feats_path, chunksize=batch_size))

    processed_sites = 0
    processed_feats = 0

    try:
        with driver.session() as session:
            # 1. Constraints (inkl. RockArtMotif)
            _create_constraints(session)

            # 2. Alte migrierte Beziehungen aufraumen (Idempotenz bei Re-Import)
            _cleanup_before_import(session)

            # 3. Sites importieren
            for batch in _read_csv_in_chunks(sites_path, batch_size):
                session.execute_write(_import_sites_batch, batch)
                processed_sites += len(batch)
                if progress_cb:
                    progress_cb("sites", processed_sites, total_sites)
            log.info("Sites importiert: %d Zeilen.", processed_sites)

            # 4. Features importieren (mit Orphan-Pruefung)
            for batch in _read_csv_in_chunks(feats_path, batch_size):
                parent_ids = {row["Site"] for row in batch if row.get("Site") is not None}
                existing = set()
                if parent_ids:
                    result = session.run(
                        "MATCH (s:Site) WHERE s.SiteID IN $ids "
                        "RETURN collect(s.SiteID) AS existing",
                        ids=list(parent_ids),
                    )
                    existing = set(result.single()["existing"]) or set()

                valid_batch = [row for row in batch if row.get("Site") in existing]
                if valid_batch:
                    session.execute_write(_import_feats_batch, valid_batch)

                processed_feats += len(batch)
                if progress_cb:
                    progress_cb("feats", processed_feats, total_feats)
            log.info("Features importiert: %d Zeilen (Orphans uebersprungen).", processed_feats)

            # 5. RockArt-Migration: Properties -> Knoten -> Properties entfernen
            log.info("Migriere RockArt-Properties zu Motiv-Knoten ...")
            _migrate_rockart_to_nodes(session)
            _remove_rockart_properties(session)

            # 6. Point-Properties setzen (Lat/Lon -> Neo4j point)
            log.info("Setze Point-Properties aus Lat/Lon ...")
            _set_point_properties(session)

            # 7. Indexe anlegen (inkl. Vektor/Fulltext mit Fehlertoleranz)
            _create_indexes(session)

            # 8. Proximity-Beziehungen (gleicher Typ)
            if create_proximity:
                _create_proximity_relationships(session, progress_cb=progress_cb)
                # 9. Cross-Proximity (Feature <-> Site, mit BoundingBox-Vorfilter)
                _create_cross_proximity(session, progress_cb=progress_cb)
            else:
                log.info("Proximity-Beziehungen uebersprungen (create_proximity=False).")

    finally:
        driver.close()
        log.info("Neo4j-Treiber geschlossen.")
