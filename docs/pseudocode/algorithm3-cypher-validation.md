# Algorithm 3: Cypher Query Validation and Execution

Three-stage validation cascade that sanitises, validates, and
auto-corrects LLM-generated Cypher queries before execution against
Neo4j.

Reference: `modules/llm.py:_handle_cypher_query()`,
`modules/disambiguator.py`

```
Algorithm 3: Cypher Query Validation and Execution
----------------------------------------------------------------------
Input : query (raw Cypher string from LLM),
        data_path (output path for JSON results)
Output: (result_text, ToolCallRecord)

  // --- Stage 1: Sanitise ---
 1  query <- RemoveEscapedNewlines(query)
 2  query <- CollapseExcessiveNewlines(query)

  // --- Stage 2: Fix deprecated syntax ---
 3  for each match of "exists(var.prop)" in query do
 4      Replace with "var.prop IS NOT NULL"
 5  end for

  // --- Stage 3: Validate string literals ---
 6  literals <- ExtractStringLiterals(query)    // regex: '[^']+'
 7  warnings <- empty list
 8  for each lit in literals do
 9      if lit not in ALL_KNOWN_VALUES then
10          closest <- FuzzyMatch(lit, ALL_KNOWN_VALUES,
                                  threshold=0.7)
11          if closest != null then
12              Append(warnings, "Unknown: lit, suggest: closest")
13          end if
14      end if
15  end for

  // --- Stage 4: Auto-correct unknown values ---
16  if warnings is not empty then
17      for each lit in literals do
18          if lit not in ALL_KNOWN_VALUES then
19              if lit in CANONICAL_MAP then
20                  query <- Replace(query, lit, CANONICAL_MAP[lit])
21              else
22                  closest <- FuzzyMatch(lit, ALL_KNOWN_VALUES)
23                  if closest != null then
24                      query <- Replace(query, lit, closest)
25                  end if
26              end if
27          end if
28      end for
29  end if

  // --- Stage 5: Execute ---
30  try
31      rows <- Neo4j.Execute(query)
32      WriteJSON(rows, data_path)
33      text <- FormatPreview(rows, limit=10)
34      return (text, SuccessRecord(query, rows))
35  catch error
36      return (FormatError(error), FailureRecord(query, error))
37  end try
```

## Validation Sources

| Source | Content | Used in |
|--------|---------|---------|
| `ALL_KNOWN_VALUES` | Union of all category, location, surface, condition values from `concepts.yml` | Lines 9, 18 |
| `CANONICAL_MAP` | Variant/typo to canonical mapping (e.g., "camp site" to "campsite") | Line 19 |
| `FuzzyMatch` | `difflib.get_close_matches` with cutoff 0.7 | Lines 10, 22 |
