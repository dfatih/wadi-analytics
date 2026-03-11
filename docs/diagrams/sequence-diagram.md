# Sequence Diagram -- Agent Tool-Use Loop

Shows the message flow for a single user question through the LLM agent
loop, including Cypher query execution and spatial analysis.

```mermaid
sequenceDiagram
    participant U as User
    participant UI as Streamlit UI
    participant D as Disambiguator
    participant A as run_agent
    participant LLM as OpenAI LLM
    participant N as Neo4j
    participant P as Python Subprocess

    U->>UI: Enter question
    UI->>D: resolve_terms(question)
    D-->>UI: ResolvedQuery

    UI->>A: run_agent(question, model)
    A->>A: render system prompt<br/>(Jinja2 template + concepts)
    A->>LLM: chat.completions.create<br/>(system, user, tools)

    loop Tool-Use Loop (max 10 iterations)
        LLM-->>A: tool_call: run_cypher_query

        A->>D: fix_cypher_syntax(query)
        D-->>A: corrected query
        A->>D: validate_cypher_values(query)
        D-->>A: warnings
        A->>D: auto_correct_cypher(query)
        D-->>A: final query + corrections

        A->>N: run_cypher(query)
        N-->>A: list[dict] result
        A->>A: save JSON to disk

        A->>LLM: tool result (row count, preview)
        LLM-->>A: tool_call: run_spatial_analysis

        A->>P: run_python_code(code)
        P->>P: load JSON, compute<br/>PySAL statistics
        P->>P: write GeoJSON + summary JSON
        P-->>A: stdout, stderr

        A->>A: load summary JSON,<br/>validate (NaN check)
        A->>LLM: tool result (stdout, summary)
        LLM-->>A: finish_reason = stop
    end

    A-->>UI: AgentResult
    UI->>UI: map AgentResult<br/>to ChatMessage
    UI-->>U: Render answer + metrics
```

## Key Observations

- The LLM autonomously decides which tools to call and in what order.
- Cypher queries pass through a 3-stage validation cascade
  (syntax fix, value validation, auto-correction) before execution.
- Spatial analysis runs in an isolated subprocess with a 900-second timeout.
- The loop terminates when the LLM returns `finish_reason = stop` or after
  10 iterations.
- Token usage and cost are accumulated across all iterations.
