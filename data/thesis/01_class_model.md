# UML-Klassendiagramm: Datenmodell

> **Quellen:** `app/chat_models.py`, `modules/chain.py`, `modules/disambiguator.py`, `modules/helper.py`

```mermaid
classDiagram
    direction TB

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
        +__post_init__() void
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

    class ChainPlan {
        +str original_question
        +list~ChainStep~ steps
        +dict~int_StepResult~ results
    }

    class ChainStep {
        +int step_index
        +str sub_question
        +str analysis_type
        +str decision_type
        +int depends_on
        +ConditionType condition
        +str context_hint
    }

    class StepResult {
        +int step_index
        +str analysis_type
        +bool success
        +str stdout
        +str stderr
        +str explanation
        +dict summary_json
        +str data_path
        +str geojson_path
        +bool skipped
        +str skip_reason
    }

    class ConditionType {
        <<enumeration>>
        NONE
        IF_SIGNIFICANT
        IF_YES
        IF_DATA
    }

    class ResolvedTerm {
        +str original_text
        +str node_type
        +str property_name
        +list~str~ resolved_values
        +str confidence
    }

    class ResolvedQuery {
        +list~ResolvedTerm~ terms
        +list~str~ warnings
        +list~str~ disambiguation_notes
    }

    class LLMResult {
        +str answer
        +dict metadata
        +__str__() str
        +strip() str
    }

    ChatMessage "1" *-- "0..*" StepRecord : step_records
    ChatMessage "1" *-- "0..1" MetricsRecord : metrics
    StepRecord "1" *-- "0..1" DisambiguationRecord : disambiguation
    ChainPlan "1" *-- "1..*" ChainStep : steps
    ChainPlan "1" o-- "0..*" StepResult : results
    ChainStep --> ConditionType : condition
    ResolvedQuery "1" *-- "0..*" ResolvedTerm : terms
```

**Hinweise:**
- `StepRecord.__post_init__()` trunciert: MAX_STDOUT=5000, MAX_CODE=10000, MAX_PREVIEW=10000
- `ChatMessage`: MAX_TURNS=50 (= 100 Nachrichten)
