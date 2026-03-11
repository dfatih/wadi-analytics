# Data Flow Diagram -- Query Processing Pipeline

Shows how data flows through the system when a user submits a research
question, from input through LLM reasoning to statistical output.

```mermaid
flowchart LR
    subgraph Input
        Q[User Question]
        C[(concepts.yml)]
        T[(Jinja2 Templates)]
    end

    subgraph Disambiguation
        R[resolve_terms]
        RQ[ResolvedQuery<br/>terms + warnings]
    end

    subgraph Agent [LLM Agent Loop]
        SP[System Prompt<br/>schema + concepts +<br/>resolved terms]
        LLM[OpenAI GPT-4.1<br/>tool-use]
    end

    subgraph CypherTool [run_cypher_query]
        FIX[fix_cypher_syntax]
        VAL[validate_cypher_values]
        AC[auto_correct_cypher]
        EX[Execute on Neo4j]
        JS[(analysis_input.json)]
    end

    subgraph AnalysisTool [run_spatial_analysis]
        PY[Python Subprocess]
        PYSAL[PySAL / ESDA<br/>Moran, LISA, Gi*]
        GEO[(GeoJSON output)]
        SUM[(summary.json)]
    end

    subgraph Output
        ANS[Agent Answer<br/>with interpretation]
        MET[Metrics<br/>tokens, cost, duration]
        MAP[Interactive Map<br/>Pydeck + H3]
    end

    Q --> R
    C --> R
    R --> RQ
    RQ --> SP
    C --> SP
    T --> SP
    SP --> LLM

    LLM -->|tool_call| FIX
    FIX --> VAL --> AC --> EX
    EX --> JS
    JS -->|result| LLM

    LLM -->|tool_call| PY
    PY --> PYSAL
    JS -.->|reads| PYSAL
    PYSAL --> GEO
    PYSAL --> SUM
    SUM -->|result| LLM

    LLM --> ANS
    LLM --> MET
    GEO --> MAP
```

## Data Artifacts

| Artifact | Format | Path | Purpose |
|----------|--------|------|---------|
| analysis_input.json | JSON array | `results/` | Cypher query results for spatial analysis |
| summary.json | JSON object | `results/visualisierung/{type}/` | Statistical results (Moran's I, p-value, n) |
| output.geojson | GeoJSON | `results/visualisierung/{type}/` | Spatial features for map visualization |
| query result | JSON | `results/queries/{ts}.json` | Persisted full query record with steps and metrics |
| comparison | JSON + CSV | `results/comparisons/{ts}.*` | Multi-model comparison results |

## Analysis Types

| Type | PySAL Method | Output Metrics |
|------|-------------|----------------|
| autocorrelation | Moran's I (global) | I, p-value, n |
| colocation | Moran_BV (bivariate) | I, p-value, n |
| hotspot | Getis-Ord Gi* | z-scores, p-values per unit |
| correlation | Pearson / Spearman | r, p-value |
| ripley_k | K-function | K(d) vs. CSR envelope |
| spatial_distance | NN-distance | distance statistics |
