# RAG Pipeline for Wadi Abu Dom Features & Sites

> Domain-specific RAG pipeline: Archaeological object data (Sites, Features, Finds) are normalized, indexed, and queried via hybrid retrieval with quality audit.

```mermaid
flowchart LR
  A["Object Data:<br/>Sites / Features / Finds"] --> B["Normalize + Metadata<br/>(ID, Period, Geocoord.)"]
  B --> C["Chunking:<br/>Site-Chunk / Feature-Chunk / Find-Chunk"]
  C --> D1["Sparse Index<br/>(BM25 + Filter)"]
  C --> D2["Dense Embeddings +<br/>Vector Index (ANN)"]
  Q["Query:<br/>Archaeological Question"] --> E["Query-Builder:<br/>Filter (Period / Region / Type)"]
  E --> F["Hybrid Retrieval:<br/>BM25 + Dense"]
  D1 --> F
  D2 --> F
  F --> G[Rerank / Dedup / Quality-Filter]
  G --> H["Prompt: Instruction +<br/>Source Chunks + Question"]
  H --> I[LLM Generates Answer + Chunk IDs]
  I --> J["Audit: Plausibility /<br/>Contradictions / Missing Evidence"]
```
