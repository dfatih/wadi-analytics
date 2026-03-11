# Algorithm 5: Embedding Generation with Deduplication Cache

Generates semantic embeddings for all graph nodes using OpenAI
text-embedding-3-small. A SHA-256 keyed DuckDB cache avoids redundant
API calls for identical text representations.

Reference: `modules/neo4j/generate_embeddings.py`

```
Algorithm 5: Embedding Generation with Deduplication Cache
----------------------------------------------------------------------
Input : table (DuckDB table name: "Sites" or "Features"),
        text_cols (list of text columns to concatenate)
Output: table updated with 1536-dimensional embedding vectors

 1  df    <- ReadDuckDB(table)
 2  cache <- LoadEmbeddingCache("embeddings.duckdb")

 3  for each row in df do
 4      if row.embedding is not null then
 5          continue                          // already embedded
 6      end if

 7      text <- Concatenate(row[c] for c in text_cols,
                            separator=" | ",
                            skip_null=true)
 8      key  <- SHA256(text)

 9      if key in cache then
10          vec <- cache[key]                 // cache hit
11      else
12          vec <- OpenAI.Embed(text,
                    model="text-embedding-3-small")  // 1536 dims
13          cache[key] <- vec                 // persist to DuckDB
14      end if

15      row.embedding <- vec
16  end for

17  WriteDuckDB(table, df)                    // replace table
```

## Text Column Configuration

| Table | Concatenated columns |
|-------|---------------------|
| Sites | Category, Location1, Location2, Surface |
| Features | Category, Location1, Location2, Condition, Age, Category2, RockArt1..6 |

## Cache Properties

- Storage: DuckDB table `emb_cache (key TEXT PK, vec BLOB)`
- Key: SHA-256 hash of concatenated text (deterministic deduplication)
- Value: pickled Python list of 1536 floats
- Path: `cache/duckdb/embeddings.duckdb`
- Benefit: avoids re-requesting embeddings for unchanged rows on re-import
