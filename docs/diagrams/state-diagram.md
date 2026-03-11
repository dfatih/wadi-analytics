# State Diagram -- LLM Agent Loop States

Shows the state transitions of the LLM agent during a single
`run_agent()` invocation, including tool dispatch and termination
conditions.

```mermaid
stateDiagram-v2
    [*] --> Initializing

    Initializing: Resolve terms, render<br/>system prompt, build context
    Initializing --> WaitingForLLM : send initial request

    WaitingForLLM: API call to<br/>OpenAI / Azure
    WaitingForLLM --> ProcessingResponse : response received

    ProcessingResponse --> Finished : finish_reason = stop
    ProcessingResponse --> DispatchingTool : tool_call received

    state DispatchingTool {
        [*] --> CheckToolName

        CheckToolName --> ExecutingCypher : run_cypher_query
        CheckToolName --> ExecutingAnalysis : run_spatial_analysis
        CheckToolName --> ToolError : unknown tool

        ExecutingCypher: Validate, correct,<br/>execute Cypher
        ExecutingCypher --> CypherDone : query result
        ExecutingCypher --> ToolError : Neo4j error

        ExecutingAnalysis: Run Python in<br/>subprocess (900s timeout)
        ExecutingAnalysis --> AnalysisDone : stdout + summary JSON
        ExecutingAnalysis --> ToolError : script error / timeout

        CypherDone --> [*]
        AnalysisDone --> [*]
        ToolError --> [*]
    }

    DispatchingTool --> IterationCheck : tool result appended

    IterationCheck: iteration < max (10)?
    IterationCheck --> WaitingForLLM : yes
    IterationCheck --> Finished : no, max reached

    Finished: Assemble AgentResult<br/>(answer, tool_calls, metrics)
    Finished --> [*]
```

## State Descriptions

| State | Module | Key Action |
|-------|--------|------------|
| Initializing | `llm.py` | `resolve_terms()`, `render_template()`, build tool schemas |
| WaitingForLLM | `helper.py` | `chat.completions.create()` with tools parameter |
| ProcessingResponse | `helper.py` | Parse `finish_reason` and `tool_calls` from response |
| ExecutingCypher | `llm.py` | 3-stage validation cascade, `run_cypher()`, save JSON |
| ExecutingAnalysis | `llm.py` | `run_python_code()`, load summary JSON, validate |
| ToolError | `llm.py` | Format error message, record in ToolCallRecord |
| IterationCheck | `helper.py` | Increment counter, compare against `max_iterations` |
| Finished | `helper.py` | Aggregate tokens, cost, duration into AgentResult |

## Termination Conditions

1. **Normal**: LLM returns `finish_reason = stop` with final answer text.
2. **Iteration limit**: 10 tool-use rounds exhausted; last partial answer returned.
3. **API error**: Exception during LLM call; error propagated to caller.
