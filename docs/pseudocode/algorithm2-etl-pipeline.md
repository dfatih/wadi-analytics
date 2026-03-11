# Algorithm 2: ETL Import Pipeline

Four-stage pipeline that transforms raw GeoPackage data into a
Neo4j knowledge graph with semantic embeddings and spatial proximity
relationships.

Reference: `modules/neo4j/gpkg_to_duckdb.py`, `generate_embeddings.py`,
`export_csv.py`, `neo4j_import.py`

```
Algorithm 2: ETL Import Pipeline
----------------------------------------------------------------------
Input : gpkg_path (GeoPackage file with Sites and Features layers)
Output: populated Neo4j graph with nodes, edges, and indexes

  // --- Stage 1: GeoPackage to DuckDB ---
 1  sites_raw  <- ReadGeoPackage(gpkg_path, layer="Sites")
 2  feats_raw  <- ReadGeoPackage(gpkg_path, layer="Features")
 3  sites      <- DropNullCoordinates(sites_raw)
 4  sites      <- RemoveDuplicates(sites, key="SiteID")
 5  feats      <- DropNullCoordinates(feats_raw)
 6  feats      <- RemoveDuplicates(feats, key="FeatureID")
 7  feats      <- FilterOrphans(feats, valid_parents=sites.SiteID)
 8  sites      <- ReprojectToWGS84(sites, from=EPSG:32636)
 9  feats      <- ReprojectToWGS84(feats, from=EPSG:32636)
10  WriteDuckDB("Sites", sites)
11  WriteDuckDB("Features", feats)

  // --- Stage 2: Embedding Generation (Algorithm 5) ---
12  for each table in {Sites, Features} do
13      GenerateEmbeddings(table)                  // Algorithm 5
14  end for

  // --- Stage 3: CSV Export ---
15  ExportCSV("Sites"  -> "data/sites_vec.csv")
16  ExportCSV("Features" -> "data/feat_vec.csv")

  // --- Stage 4: Neo4j Graph Construction ---
17  CreateConstraints(SiteID, FeatureID, RockArtMotif.name)
18  CleanupOldRelationships(HAS_ROCKART, NEAR, LOCATED_ON)

19  for each batch in ReadCSVChunked(sites_csv, size=1000) do
20      MERGE Site nodes with properties from batch
21  end for

22  for each batch in ReadCSVChunked(feats_csv, size=1000) do
23      valid <- FilterToExistingParents(batch)
24      MERGE Feature nodes from valid
25      MERGE HAS_FEATURE edges to parent Sites
26  end for

27  MigrateRockArt()      // RockArt1..6 -> RockArtMotif nodes
28  SetPointProperties()  // Lat/Lon -> Neo4j point(WGS-84)
29  CreateIndexes()       // property, point, vector, fulltext

  // --- Proximity Computation ---
30  for each pair (a, b) in Sites x Sites do
31      if a.id < b.id and Distance(a, b) < 500m then
32          MERGE (a)-[:CLOSE_TO_SITE {distance_m}]->(b)
33  end for

34  for each pair (a, b) in Features x Features do
35      if a.id < b.id and Distance(a, b) < 500m then
36          MERGE (a)-[:CLOSE_TO_FEATURE {distance_m}]->(b)
37  end for

38  for each (f, s) in Features x Sites do
39      if s != Parent(f) and Distance(f, s) < 500m then
40          MERGE (f)-[:NEAR {distance_m}]->(s)
41      end if               // uses bounding-box pre-filter
42  end for
```

## Notes

- Stage 4 lines 30--42 use `point.distance()` in Cypher, executed
  server-side in Neo4j. The bounding-box pre-filter (line 41) converts
  the distance threshold to a latitude/longitude delta
  (delta = 500 / 111000 degrees) to avoid a full cross join.
- Batch size of 1000 rows per transaction balances throughput and memory.
- The pipeline is idempotent: re-running cleans up migrated relationships
  first (line 18).
