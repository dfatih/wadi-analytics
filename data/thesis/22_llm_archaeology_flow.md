# LLM Algorithm Flow for Archaeological Use

> Adapted LLM flow for archaeological applications: question in German, instruction schema, evidence checking, and transparent handling of missing data.

```mermaid
flowchart TD
  A["Archaeological Question<br/>(German)"] --> B["Tokenization<br/>(Base Model)"]
  B --> C[Representation in Context Window]
  C --> D[Activate Instruction & Response Schema]
  D --> E[LLM Generates Draft Answer]
  E --> F{Evidence Available?}
  F -->|yes| G[Cite Sources / Chunk IDs]
  F -->|no| H[Flag Uncertainty + Missing Data]
  G --> I[Final Answer]
  H --> I
```
