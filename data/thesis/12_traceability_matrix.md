# Traceability Matrix -- Diagramme und Pseudocode zu Quellcode

## Pflichtpaket

| # | Artefakt | Typ | Primaere Quell-Dateien | Funktionen / Klassen | Zeilen |
|---|----------|-----|------------------------|----------------------|--------|
| 1 | 01_class_model.md | classDiagram | `app/chat_models.py` | `ChatMessage`, `StepRecord`, `MetricsRecord`, `DisambiguationRecord`, `_truncate`, `enforce_turn_limit` | 1-115 |
| | | | `modules/chain.py` | `ChainPlan`, `ChainStep`, `StepResult`, `ConditionType` | 21-58 |
| | | | `modules/disambiguator.py` | `ResolvedTerm`, `ResolvedQuery` | 26-38 |
| | | | `modules/helper.py` | `LLMResult` | 33-43 |
| 2 | 02_component_architecture.md | flowchart LR | `app/main.py` | Imports, `main()` | 1-30 |
| | | | `app/ui_chat.py` | Imports | 11-47 |
| | | | `modules/llm.py` | Imports | 1-15 |
| | | | `modules/helper.py` | Imports | 1-22 |
| | | | `modules/disambiguator.py` | Imports | 7-17 |
| 3 | 03_sequence_normal_chat.md | sequenceDiagram | `app/ui_chat.py` | `run_chat()`, `_run_normal_mode()` | 519-574, 294-409 |
| | | | `modules/llm.py` | `decompose_question()` | 372+ |
| | | | `modules/chain.py` | `evaluate_condition()`, `build_prior_context()` | 64-87, 153-170 |
| | | | `app/ui_chat.py` | `_execute_cypher_step()`, `_execute_python_step()` | 159-192, 195-288 |
| | | | `modules/helper.py` | `drain_llm_results()` | 52-60 |
| | | | `app/ui_chat.py` | `_build_metrics()`, `enforce_turn_limit()` | 87-97 |
| | | | `app/chat_renderer.py` | `render_chat_message()` | 19-51 |
| 4 | 04_sequence_python_step.md | sequenceDiagram | `app/ui_chat.py` | `_execute_python_step()` | 195-288 |
| | | | `modules/llm.py` | `extract_relevant_data()` | Aufruf von `resolve_terms`, `call_llm_with_prompt`, `run_cypher` |
| | | | `modules/llm.py` | `generate_analysis_code()` | Aufruf von `resolve_terms`, `call_llm_with_prompt` |
| | | | `modules/helper.py` | `run_python_code()` | 282-296 |
| | | | `app/ui_chat.py` | `_load_summary_json()`, `_validate_summary_json()` | 100-153 |
| | | | `modules/llm.py` | `explain_de()` | Aufruf von `call_llm_with_prompt` |
| | | | `app/ui_chat.py` | `_collect_disambiguation()` | 55-84 |
| 5 | 05_sequence_comparison_mode.md | sequenceDiagram | `app/ui_chat.py` | `_run_comparison_mode()` | 442-499 |
| | | | `app/ui_chat.py` | `_aggregate_metrics_dict()`, `_persist_comparison()` | 415-439 |
| | | | `app/chat_renderer.py` | `_render_comparison()` | 203-252 |
| 6 | 06_activity_chain_execution.md | flowchart TD | `app/ui_chat.py` | `_run_normal_mode()` | 294-409 |
| | | | `modules/chain.py` | `evaluate_condition()` | 64-87 |
| | | | `modules/chain.py` | `_check_significance()` | 90-128 |
| | | | `modules/chain.py` | `_check_affirmative()` | 131-138 |
| | | | `modules/chain.py` | `_check_has_data()` | 141-147 |
| | | | `modules/chain.py` | `build_prior_context()` | 153-170 |
| 7 | 07_er_neo4j_schema.md | erDiagram | `modules/neo4j/neo4j_import.py` | `_create_constraints()` | 18-25 |
| | | | `modules/neo4j/neo4j_import.py` | `_import_sites_batch()`, `_import_feats_batch()` | 264-319 |
| | | | `modules/neo4j/neo4j_import.py` | `_create_indexes()` | 31-81 |
| | | | `modules/neo4j/neo4j_import.py` | `_create_proximity_relationships()` | 159-200 |
| | | | `modules/neo4j/neo4j_import.py` | `_create_cross_proximity()` | 206-239 |
| | | | `modules/neo4j/neo4j_import.py` | `_migrate_rockart_to_nodes()` | 128-153 |
| 8 | 08_deployment_docker.md | flowchart LR | `docker-compose.yml` | Services: neo4j, streamlit-app | 3-59 |
| | | | `Dockerfile` | Build-Stages, PYTHONPATH, ENTRYPOINT | - |
| | | | `.env` (Schema) | NEO4J_URI, OPENAI_API_KEY, etc. | - |
| 9 | 09_import_pipeline.md | flowchart TD | `modules/neo4j/neo4j_import.py` | `import_to_neo4j()` | 325-419 |
| | | | | `_create_constraints()` | 18-25 |
| | | | | `_cleanup_before_import()` | 87-100 |
| | | | | `_import_sites_batch()` | 264-282 |
| | | | | `_import_feats_batch()` | 288-319 |
| | | | | `_migrate_rockart_to_nodes()` | 128-143 |
| | | | | `_remove_rockart_properties()` | 146-153 |
| | | | | `_set_point_properties()` | 106-122 |
| | | | | `_create_indexes()` | 31-81 |
| | | | | `_create_proximity_relationships()` | 159-200 |
| | | | | `_create_cross_proximity()` | 206-239 |
| 10 | 10_llm_call_pipeline.md | sequenceDiagram | `modules/helper.py` | `call_llm_with_prompt()` | 180-261 |
| | | | `modules/helper.py` | `_calculate_cost()` | 108-128 |
| | | | `modules/helper.py` | `get_model_config()` | 83-97 |
| | | | `modules/helper.py` | `render_template()` | 149-157 |
| | | | `modules/helper.py` | `drain_llm_results()` | 52-60 |
| | | | `modules/logger.py` | `log_result()` | 79+ |

## Pseudocode (11_pseudocode_core_algorithms.md)

| # | Algorithmus | Quell-Datei | Funktion | Zeilen |
|---|-------------|-------------|----------|--------|
| 1 | decompose_question | `modules/llm.py` | `decompose_question()` | 372-441 |
| 1 | _fallback_plan | `modules/llm.py` | `_fallback_plan()` | 444-455 |
| 2 | evaluate_condition | `modules/chain.py` | `evaluate_condition()` | 64-87 |
| 2 | _check_significance | `modules/chain.py` | `_check_significance()` | 90-128 |
| 2 | _check_affirmative | `modules/chain.py` | `_check_affirmative()` | 131-138 |
| 2 | _check_has_data | `modules/chain.py` | `_check_has_data()` | 141-147 |
| 3 | _execute_cypher_step | `app/ui_chat.py` | `_execute_cypher_step()` | 159-192 |
| 4 | _execute_python_step | `app/ui_chat.py` | `_execute_python_step()` | 195-288 |
| 5 | resolve_terms | `modules/disambiguator.py` | `resolve_terms()` | 151-204 |
| 5 | _resolve_single | `modules/disambiguator.py` | `_resolve_single()` | 207-239 |
| 5 | _pick_best_location | `modules/disambiguator.py` | `_pick_best_location()` | 242-272 |
| 6 | _validate_summary_json | `app/ui_chat.py` | `_validate_summary_json()` | 123-153 |
| 7 | _run_normal_mode | `app/ui_chat.py` | `_run_normal_mode()` | 294-409 |
| 8 | _run_comparison_mode | `app/ui_chat.py` | `_run_comparison_mode()` | 442-499 |
| 9 | run_chat | `app/ui_chat.py` | `run_chat()` | 519-574 |
| 10 | call_llm_with_prompt | `modules/helper.py` | `call_llm_with_prompt()` | 180-261 |
| 11 | _calculate_cost | `modules/helper.py` | `_calculate_cost()` | 108-128 |
| 12 | run_python_code | `modules/helper.py` | `run_python_code()` | 282-296 |
| 12 | _clean | `modules/helper.py` | `_clean()` | 273-279 |
| 13 | generate_cypher | `modules/llm.py` | `generate_cypher()` | 139-185 |
| 14 | extract_relevant_data | `modules/llm.py` | `extract_relevant_data()` | 223-283 |
| 15 | generate_analysis_code | `modules/llm.py` | `generate_analysis_code()` | 73-136 |
| 16 | _merge_comparative_steps | `modules/llm.py` | `_merge_comparative_steps()` | 297-369 |
| 17 | _collect_disambiguation | `app/ui_chat.py` | `_collect_disambiguation()` | 55-84 |
| 18 | build_prior_context | `modules/chain.py` | `build_prior_context()` | 153-170 |
| 19 | validate_cypher_values | `modules/disambiguator.py` | `validate_cypher_values()` | 278-296 |
| 19 | auto_correct_cypher | `modules/disambiguator.py` | `auto_correct_cypher()` | 299-317 |
| 19 | _find_closest_match | `modules/disambiguator.py` | `_find_closest_match()` | 320-323 |
| 20 | _build_metrics | `app/ui_chat.py` | `_build_metrics()` | 87-97 |
| 21 | enforce_turn_limit | `app/chat_models.py` | `enforce_turn_limit()` | 107-114 |

## Optionale Erweiterungen

| # | Artefakt | Typ | Primaere Quell-Dateien | Zeilen |
|---|----------|-----|------------------------|--------|
| 13 | 13_state_chat_message_lifecycle.md | stateDiagram-v2 | `app/ui_chat.py` | 519-574 |
| 14 | 14_error_handling_flow.md | flowchart TD | `app/ui_chat.py`, `modules/disambiguator.py` | 159-288, 278-317 |
| 15 | 15_testing_architecture.md | flowchart LR | `tests/conftest.py`, `tests/test_*.py` | - |
| 16 | 16_security_architecture_asis_tobe.md | flowchart LR | `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `modules/logger.py` | - |
| 17 | 17_research_methodology.md | flowchart TD | `templates/system/*.jinja2`, `app/ui_chat.py` | - |
