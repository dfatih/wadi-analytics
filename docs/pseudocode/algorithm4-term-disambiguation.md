# Algorithm 4: Deterministic Term Disambiguation

Resolves archaeological terms from a natural-language question into
canonical Neo4j node types, property names, and values -- without any
LLM call. Uses word-boundary matching, German alias resolution, and
domain-specific disambiguation rules.

Reference: `modules/disambiguator.py:resolve_terms()`

```
Algorithm 4: Deterministic Term Disambiguation
----------------------------------------------------------------------
Input : question (natural-language research question)
Output: ResolvedQuery (terms[], warnings[], notes[])

 1  result  <- new ResolvedQuery()
 2  q_lower <- Lowercase(question)
 3  q_norm  <- NormaliseUmlauts(q_lower)   // ae->ae, oe->oe, ue->ue

  // --- Phase 1: German alias resolution ---
 4  for each (alias, targets) in TERM_ALIASES do
 5      if WordBoundaryMatch(alias, q_norm) then
 6          for each target in targets do
 7              ResolveSingle(target, result, confidence="alias",
                              original=alias)
 8          end for
 9      end if
10  end for

  // --- Phase 2: Direct value matching ---
11  for each (value, locations) in VALUE_INDEX do
12      if Length(value) < 3 then continue       // skip short values
13      if WordBoundaryMatch(value, q_lower) then
14          if value not already resolved in result then
15              ResolveSingle(value, result, confidence="exact",
                              original=value)
16          end if
17      end if
18  end for

  // --- Phase 3: Indicator group matching ---
19  for each group in {grave, mobility, sedentary, water, stone} do
20      if group_name in q_lower then
21          for each (node_type, values) in group.entries do
22              for each v in values do
23                  Append(result.terms, ResolvedTerm(
24                      original=group_name, node_type,
25                      property="Category", values=[v],
26                      confidence="group"))
27              end for
28          end for
29      end if
30  end for

31  return result


Subroutine ResolveSingle(value, result, confidence, original)
----------------------------------------------------------------------
 1  locations <- VALUE_INDEX[Lowercase(value)]
 2  if locations is empty then return
 3  canonical <- CANONICAL_MAP.get(value, default=value)
 4  unique    <- RemoveDuplicates(locations)

 5  if |unique| = 1 then
 6      (node_type, prop) <- unique[0]
 7  else
 8      (node_type, prop) <- PickBestLocation(value, unique)
 9      Append(result.notes, disambiguation message)
10  end if

11  Append(result.terms, ResolvedTerm(original, node_type,
          prop, [canonical], confidence))


Subroutine PickBestLocation(value, locations)
----------------------------------------------------------------------
  // Priority: Location terms -> Feature-only -> Site-only -> Feature
 1  if value in LOCATION_TERMS then
 2      return first (type, "Location1") where type="Feature"
 3  if value in FEATURE_ONLY_CATEGORIES then
 4      return ("Feature", "Category")
 5  if value in SITE_ONLY_CATEGORIES then
 6      return ("Site", "Category")
 7  return first ("Feature", "Category") in locations, else locations[0]
```

## Index Construction (module initialisation)

Built once at import time from `config/concepts.yml`:

| Index | Source sections | Example |
|-------|----------------|---------|
| `TERM_ALIASES` | `german_aliases` | "Friedhof" -> [tumulus, box grave, ...] |
| `VALUE_INDEX` | `category_map`, `location1_map`, `location2_map`, `surface_types`, `rockart_motifs`, `category_variants`, `data_corrections` | "tumulus" -> [(Feature, Category)] |
| `ALL_KNOWN_VALUES` | all of the above | {"tumulus", "ridge", "well", ...} |
| `CANONICAL_MAP` | `category_variants`, `data_corrections` | "camp site" -> "campsite" |
| `FEATURE_ONLY_CATEGORIES` | `disambiguation_rules` | {tumulus, hut, shelter, ...} |
| `SITE_ONLY_CATEGORIES` | `disambiguation_rules` | {big ruins, rock arts} |
| `LOCATION_TERMS` | `location_terms` | {ridge, terrace, slope, ...} |
