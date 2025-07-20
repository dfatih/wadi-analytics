/* -------------------------------------------------------------
   extract_concepts_for_yaml_v2.cypher   (Neo4j 5‑kompatibel)
   -----------------------------------------------------------*/
/* 1 ─ Direct property maps ─────────────────────────────────── */
MATCH (n)
WITH
  CASE
    WHEN n:Site THEN 'Site'
    ELSE 'Feature'
  END AS source_table,
  [
    ['Category Map', n.Category],
    ['Category2 Map', n.Category2],
    ['Location1 Map', n.Location1],
    ['Location2 Map', n.Location2],
    ['Surface Types', n.Surface]
  ] AS kv
UNWIND kv AS pair
WITH pair[0] AS key, source_table, toLower(trim(toString(pair[1]))) AS value
WHERE value IS NOT NULL AND value <> '' AND value <> 'nan'
RETURN key, source_table, value, count(*) AS occurrences

  UNION ALL
/* 2 ─ Rock‑art motifs ─────────────────────────────────────── */
MATCH (f:Feature)
UNWIND [f.RockArt1, f.RockArt2, f.RockArt3, f.RockArt4, f.RockArt5, f.RockArt6] AS
  motif
WITH
  'rockart motifs' AS key,
  'Feature' AS source_table,
  toLower(trim(toString(motif))) AS value
WHERE value IS NOT NULL AND value <> '' AND value <> 'nan'
RETURN key, source_table, value, count(*) AS occurrences

  UNION ALL
/* 3a ─ Sedentary / Mobility indicators ───────────────────── */
MATCH (n)
WITH
  CASE
    WHEN n:Site THEN 'Site'
    ELSE 'Feature'
  END AS source_table,
  coalesce(toLower(toString(n.Category)), '') +
  '|' +
  coalesce(toLower(toString(n.Category2)), '') AS cat_str
WITH
  source_table,
  CASE
    WHEN
      cat_str =~ '.*(settlement|village|farm|temple).*'
      THEN
        [
          'Sedentary Indicators',
          apoc.text.regexGroups(cat_str, '(settlement|village|farm|temple)')[0]
        ]
    WHEN
      cat_str =~ '.*(camp|encampment|temporary).*'
      THEN
        [
          'Mobility Indicators',
          apoc.text.regexGroups(cat_str, '(camp|encampment|temporary)')[0]
        ]
  END AS res
WHERE res IS NOT NULL
RETURN res[0] AS key, source_table, res[1] AS value, count(*) AS occurrences

  UNION ALL
/* 3b ─ Water / Stone / Grave indicators ─────────────────── */
MATCH (n)
WITH
  CASE
    WHEN n:Site THEN 'Site'
    ELSE 'Feature'
  END AS source_table,
  coalesce(toLower(toString(n.Category)), '') +
  '|' +
  coalesce(toLower(toString(n.Category2)), '') AS cat_str
UNWIND [
  ['Water Indicators', 'well|cistern|canal|harbor|spring'],
  ['Stone Indicators', 'quarry|rock|stone'],
  ['Grave Indicators', 'grave|cemetery|tomb|burial']
] AS grp
WITH source_table, cat_str, grp[0] AS key, grp[1] AS regex
WHERE cat_str =~ '.*(' + regex + ').*'
WITH
  source_table,
  key,
  apoc.text.regexGroups(cat_str, '(' + regex + ')')[0] AS value
RETURN key, source_table, value, count(*) AS occurrences

  UNION ALL
/* 4 ─ Location terms (tokens > 2 Zeichen) ────────────────── */
MATCH (n)
WITH
  CASE
    WHEN n:Site THEN 'Site'
    ELSE 'Feature'
  END AS source_table,
  [n.Location1, n.Location2] AS locs
UNWIND locs AS loc
WITH source_table, toLower(trim(toString(loc))) AS loc
WHERE loc IS NOT NULL AND loc <> '' AND loc <> 'nan'
UNWIND apoc.text.split(loc, '[^a-zA-Z0-9]+') AS token
WITH source_table, token
WHERE size(token) > 2
RETURN
  'Location Terms' AS key,
  source_table,
  token AS value,
  count(*) AS occurrences

/* 5 ─ Final ordering ─────────────────────────────────────── */
ORDER BY key, occurrences DESC;