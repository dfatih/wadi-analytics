# Component Diagram -- System Architecture

Shows the high-level component structure, their interfaces, and
external dependencies. Corresponds to the Docker Compose deployment.

```mermaid
flowchart TB
    subgraph Docker [Docker Compose Environment]
        subgraph App [Streamlit Application Container]
            subgraph UI [Presentation Layer]
                main[main.py<br/>Navigation + Sidebar]
                chat[ui_chat.py<br/>Chat Interface]
                map[ui_map.py<br/>Map View]
                import_ui[ui_import.py<br/>Import Pipeline]
                comp[ui_comparison.py<br/>Dashboard]
                render[chat_renderer.py<br/>Message Rendering]
                models[chat_models.py<br/>Data Models]
                css[css.py<br/>Styling]
            end

            subgraph Core [Core Logic Layer]
                llm[llm.py<br/>Agent Loop]
                helper[helper.py<br/>LLM + Neo4j + Subprocess]
                disamb[disambiguator.py<br/>Term Resolution]
                vis[visualization.py<br/>Pydeck Maps]
                stats[statistics.py<br/>Friedman Test]
                logger[logger.py<br/>Logging]
            end

            subgraph Pipeline [ETL Pipeline Layer]
                gpkg[gpkg_to_duckdb.py]
                embed[generate_embeddings.py]
                csv_exp[export_csv.py]
                neo_imp[neo4j_import.py]
            end

            subgraph Config [Configuration]
                models_yml[(models.yml)]
                concepts_yml[(concepts.yml)]
                patterns_yml[(analysis_patterns.yml)]
                templates[(Jinja2 Templates)]
            end
        end

        subgraph Neo4j [Neo4j Container]
            graph[(Graph Database<br/>Sites, Features,<br/>RockArtMotifs)]
        end
    end

    subgraph External [External Services]
        openai([OpenAI API<br/>GPT-4.1 + Embeddings])
        azure([Azure AI Foundry<br/>Dev Deployments])
    end

    chat --> llm
    chat --> render
    import_ui --> gpkg
    import_ui --> embed
    import_ui --> csv_exp
    import_ui --> neo_imp
    map --> vis
    comp -.-> models

    llm --> helper
    llm --> disamb
    helper --> graph
    helper --> openai
    helper --> azure
    embed --> openai
    neo_imp --> graph
    llm --> templates
    llm --> concepts_yml
    helper --> models_yml
    stats -.-> models
```

## Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| Presentation Layer | Streamlit pages, chat rendering, CSS theming |
| Core Logic Layer | LLM agent loop, Neo4j queries, term disambiguation, visualization |
| ETL Pipeline Layer | GeoPackage ingestion, embedding generation, Neo4j graph construction |
| Configuration | Model registry, domain ontology, analysis patterns, prompt templates |
| Neo4j Container | Graph storage with property, point, vector, and fulltext indexes |
| External Services | LLM inference (GPT-4.1) and embedding generation (text-embedding-3-small) |

## Interface Protocols

| Interface | Protocol | Details |
|-----------|----------|---------|
| App to Neo4j | Bolt (port 7687) | Cypher queries via `neo4j` Python driver |
| App to OpenAI | HTTPS | `openai` Python SDK, chat completions + embeddings |
| App to Azure | HTTPS | Same SDK, Azure AI Foundry endpoints |
| Streamlit UI | HTTP (port 8501) | Browser-based interactive interface |
