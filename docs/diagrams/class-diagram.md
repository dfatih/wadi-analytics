# Class Diagram -- Domain Model and Dataclasses

Shows all dataclasses, their attributes, and relationships across the
`modules/` and `app/` layers. Arrows denote composition (diamond) or
usage (dashed).

```mermaid
classDiagram
    direction TB

    class AgentResult {
        +str answer
        +list~ToolCallRecord~ tool_calls
        +int prompt_tokens
        +int completion_tokens
        +int reasoning_tokens
        +int total_tokens
        +float cost_usd
        +float duration_seconds
        +str model
    }

    class ToolCallRecord {
        +str tool_name
        +dict arguments
        +str result_text
        +bool success
        +str cypher_query
        +str python_code
        +str stdout
        +str stderr
        +str geojson_path
        +dict summary_json
    }

    class LLMResult {
        +str answer
        +dict metadata
    }

    class ResolvedQuery {
        +list~ResolvedTerm~ terms
        +list~str~ warnings
        +list~str~ disambiguation_notes
    }

    class ResolvedTerm {
        +str original_text
        +str node_type
        +str property_name
        +list~str~ resolved_values
        +str confidence
    }

    class ChatMessage {
        +str role
        +str timestamp
        +str text
        +list~dict~ plan_steps
        +list~StepRecord~ step_records
        +MetricsRecord metrics
        +bool is_comparison
        +list~dict~ comparison_table
    }

    class StepRecord {
        +int step_index
        +str analysis_type
        +str decision_type
        +str sub_question
        +bool success
        +str explanation
        +str stdout
        +str stderr
        +str cypher_query
        +str python_code
        +str cypher_preview
        +str geojson_path
        +bool skipped
        +str skip_reason
        +DisambiguationRecord disambiguation
    }

    class MetricsRecord {
        +int prompt_tokens
        +int completion_tokens
        +int reasoning_tokens
        +int total_tokens
        +float cost_usd
        +float duration_seconds
        +list~str~ models_used
    }

    class DisambiguationRecord {
        +list~dict~ terms
        +list~str~ notes
    }

    class FriedmanResult {
        +float statistic
        +float p_value
        +int n_models
        +int n_questions
        +bool is_significant
        +str metric_name
        +dict rank_means
        +NemenyiResult nemenyi
    }

    class NemenyiResult {
        +dict p_values
        +list~tuple~ significant_pairs
        +float critical_difference
    }

    AgentResult *-- "0..*" ToolCallRecord
    ResolvedQuery *-- "0..*" ResolvedTerm
    ChatMessage *-- "0..*" StepRecord
    ChatMessage *-- "0..1" MetricsRecord
    StepRecord *-- "0..1" DisambiguationRecord
    FriedmanResult *-- "0..1" NemenyiResult

    ChatMessage ..> AgentResult : created from
    StepRecord ..> ToolCallRecord : mapped from
    MetricsRecord ..> AgentResult : extracted from
    DisambiguationRecord ..> ResolvedQuery : mapped from
```

## Module Ownership

| Class              | Module                |
|--------------------|-----------------------|
| AgentResult        | `modules/helper.py`   |
| ToolCallRecord     | `modules/helper.py`   |
| LLMResult          | `modules/helper.py`   |
| ResolvedQuery      | `modules/disambiguator.py` |
| ResolvedTerm       | `modules/disambiguator.py` |
| ChatMessage        | `app/chat_models.py`  |
| StepRecord         | `app/chat_models.py`  |
| MetricsRecord      | `app/chat_models.py`  |
| DisambiguationRecord | `app/chat_models.py` |
| FriedmanResult     | `modules/statistics.py` |
| NemenyiResult      | `modules/statistics.py` |
