# Pseudocode der Kernalgorithmen -- Wadi-Analytics

## 1. decompose_question -- Mehrstufige Fragezerlegung

**Quelle:** `modules/llm.py:372` (decompose_question), `modules/llm.py:297` (_fallback_plan)

### Input
- `question`: Natuerlichsprachliche Nutzerfrage (string)
- `model`: LLM-Modellname (string, optional)

### Output
- `ChainPlan`: Geordnete Analyseschritte mit Abhaengigkeiten und Bedingungen

### Algorithmus

```
FUNCTION decompose_question(question, model):
    analysis_patterns = load_yaml("analysis_patterns.yml")

    prompt = render_template("decompose_question.jinja2", {
        "question": question,
        "analysis_patterns": analysis_patterns
    })

    llm_result = call_llm_with_prompt(
        function_name = "decompose_question",
        question      = question,
        prompt        = prompt,
        model         = model
    )

    TRY:
        raw = strip_code_fences(llm_result.answer)
        plan_dict = JSON.parse(raw)

        plan = NEW ChainPlan(original_question = question)

        FOR EACH step_dict IN plan_dict["steps"]:
            condition = ConditionType(step_dict.get("condition", "none"))

            step = NEW ChainStep(
                step_index    = step_dict["step_index"],
                sub_question  = step_dict["sub_question"],
                analysis_type = step_dict["analysis_type"],
                decision_type = step_dict["decision_type"],
                depends_on    = step_dict.get("depends_on", None),
                condition     = condition,
                context_hint  = step_dict.get("context_hint", "")
            )
            plan.steps.APPEND(step)
        END FOR

        // Sicherheitspruefung: Komparative Schritte zusammenfuehren
        plan = _merge_comparative_steps(plan)

        RETURN plan

    CATCH (JSONDecodeError, KeyError, ValueError):
        LOG.warning("Zerlegung fehlgeschlagen, Fallback auf Einzelschritt")
        RETURN _fallback_plan(question, model)
END FUNCTION


FUNCTION _fallback_plan(question, model):
    // Klassifiziert die Frage in einen einzelnen Analysetyp
    prompt = render_template("classify_analysis_type.jinja2", {
        "question": question,
        "analysis_patterns": load_yaml("analysis_patterns.yml")
    })

    result = call_llm_with_prompt("classify_analysis_type", question, prompt, model)
    parsed = load_llm_json(result.answer)

    analysis_type = parsed.get("analysis_type", "cypher")
    decision_type = parsed.get("decision_type", "cypher")

    RETURN NEW ChainPlan(
        original_question = question,
        steps = [NEW ChainStep(
            step_index    = 1,
            sub_question  = question,
            analysis_type = analysis_type,
            decision_type = decision_type
        )]
    )
END FUNCTION
```

---

## 2. evaluate_condition -- Bedingte Schrittausfuehrung

**Quelle:** `modules/chain.py:64` (evaluate_condition), `:90` (_check_significance), `:131` (_check_affirmative), `:141` (_check_has_data)

### Input
- `condition`: ConditionType (NONE, IF_SIGNIFICANT, IF_YES, IF_DATA)
- `predecessor`: StepResult des Vorgaengerschritts (optional)

### Output
- `(should_run, reason)`: Tupel aus Boolean und Begruendung

### Algorithmus

```
FUNCTION evaluate_condition(condition, predecessor):
    IF condition == NONE:
        RETURN (true, "")

    IF predecessor IS NULL:
        RETURN (true, "Kein Vorgaenger; wird bedingungslos ausgefuehrt")

    IF NOT predecessor.success:
        RETURN (false, "Vorgaenger-Schritt {predecessor.step_index} fehlgeschlagen")

    SWITCH condition:
        CASE IF_SIGNIFICANT: RETURN _check_significance(predecessor)
        CASE IF_YES:         RETURN _check_affirmative(predecessor)
        CASE IF_DATA:        RETURN _check_has_data(predecessor)
        DEFAULT:             RETURN (true, "")
END FUNCTION


FUNCTION _check_significance(result):
    // Prioritaet 1: Strukturiertes summary_json
    IF result.summary_json IS NOT NULL:
        p = result.summary_json.get("p_value")
             OR result.summary_json.get("p-value")
        IF p IS NOT NULL:
            TRY:
                IF FLOAT(p) < 0.05:
                    RETURN (true, "Signifikant (p={p:.4f} < 0.05)")
                ELSE:
                    RETURN (false, "Nicht signifikant (p={p:.4f} >= 0.05)")
            CATCH ValueError: // weiter zum Fallback

    // Prioritaet 2: Regex auf stdout
    IF result.stdout IS NOT EMPTY:
        match = REGEX("p[_-]?value\s*[:=]\s*([\d.eE-]+)", result.stdout)
        IF match:
            TRY:
                p = FLOAT(match.group(1))
                IF p < 0.05: RETURN (true, "Signifikant (aus stdout)")
                ELSE:        RETURN (false, "Nicht signifikant (aus stdout)")
            CATCH ValueError: // weiter

        // Prioritaet 3: Textuelle Pruefung
        IF "not significant" IN result.stdout: RETURN (false, "not significant")
        IF "significant" IN result.stdout:     RETURN (true, "significant")

    // Konservativ: uebersprungen wenn unklar
    RETURN (false, "Signifikanz nicht ermittelbar; uebersprungen")
END FUNCTION


FUNCTION _check_affirmative(result):
    text = (result.explanation + " " + result.stdout).to_lowercase()

    positiv  = ["yes", "ja", "found", "detected", "vorhanden"]
    negativ  = ["no", "nein", "not found", "none", "keine"]

    IF ANY keyword IN positiv WHERE keyword IN text:
        RETURN (true, "Positives Ergebnis erkannt")
    IF ANY keyword IN negativ WHERE keyword IN text:
        RETURN (false, "Negatives Ergebnis; uebersprungen")

    RETURN (true, "Ja/Nein nicht bestimmbar; wird ausgefuehrt")
END FUNCTION


FUNCTION _check_has_data(result):
    IF result.data_path OR result.geojson_path:
        RETURN (true, "Daten vom Vorgaenger vorhanden")
    IF result.stdout AND result.stdout.strip() != "":
        RETURN (true, "Vorgaenger hat Ausgaben erzeugt")
    RETURN (false, "Keine Daten vom Vorgaenger; uebersprungen")
END FUNCTION
```

---

## 3. _execute_cypher_step -- Cypher-Abfrage-Ausfuehrung

**Quelle:** `app/ui_chat.py:159`

### Input
- `question`: Teilfrage fuer diesen Schritt (string)
- `model`: LLM-Modellname (string)

### Output
- `(StepResult, cypher_query, cypher_preview, DisambiguationRecord)`

### Algorithmus

```
FUNCTION _execute_cypher_step(question, model):
    result = NEW StepResult(analysis_type="cypher", success=false)
    cypher_query   = ""
    cypher_preview = ""
    disambiguation = NEW DisambiguationRecord()

    TRY:
        // generate_cypher ruft intern resolve_terms() auf
        query = generate_cypher(question, model=model)
        cypher_query = STRING(query)

        rows = run_cypher(cypher_query)

        preview = rows[0:10] IF IS_LIST(rows) ELSE rows
        cypher_preview = JSON.stringify(preview)

        explanation = explain_cypher_result(question, rows, model=model)

        disambiguation = _collect_disambiguation()

        result.success     = true
        result.stdout      = cypher_preview
        result.explanation = STRING(explanation)

    CATCH Exception AS e:
        LOG.error("Fehler bei Cypher-Ausfuehrung: %s", e)
        disambiguation = _collect_disambiguation()
        result.stderr = STRING(e)

    RETURN (result, cypher_query, cypher_preview, disambiguation)
END FUNCTION
```

---

## 4. _execute_python_step -- Raum-statistische Analyse

**Quelle:** `app/ui_chat.py:195`

### Input
- `question`: Teilfrage (string)
- `analysis_type`: Analysetyp (z.B. "autocorrelation", "colocation")
- `model`: LLM-Modellname (string)
- `data_path`: Pfad fuer Eingabedaten (default: "results/analysis_input.json")
- `started_at`: Zeitstempel fuer Datei-Filterung (float)

### Output
- `(StepResult, python_code, DisambiguationRecord)`

### Algorithmus

```
FUNCTION _execute_python_step(question, analysis_type, model, data_path, started_at):
    result    = NEW StepResult(analysis_type=analysis_type, success=false)
    code      = ""
    disamb    = NEW DisambiguationRecord()

    // --- Phase 1: Datenextraktion ---
    TRY:
        extract_relevant_data(question, path=data_path, model=model)
        // Intern: resolve_terms() → Cypher generieren → ausfuehren → JSON speichern
    CATCH Exception AS e:
        disamb = _collect_disambiguation()
        result.stderr = STRING(e)
        RETURN (result, code, disamb)

    // --- Phase 2: Code-Generierung ---
    TRY:
        outputs = generate_analysis_code(question, analysis_type, model, data_path)
        // Intern: resolve_terms() → Template rendern → LLM aufrufen
    CATCH Exception AS e:
        disamb = _collect_disambiguation()
        result.stderr = STRING(e)
        RETURN (result, code, disamb)

    // Passenden Output finden
    current = FIND output IN outputs WHERE output["analysis_type"] == analysis_type
    IF current IS NULL:
        disamb = _collect_disambiguation()
        RETURN (result, code, disamb)

    code = current["code"]

    // --- Phase 3: Ausfuehrung ---
    (stdout, stderr) = run_python_code(code)  // subprocess, timeout=900s

    has_error = stderr CONTAINS "Traceback" OR "Error" OR "Exception"

    // GeoJSON suchen
    geojson_path = ""
    geojson_files = GLOB("results/visualisierung/{analysis_type}/*.geojson")
    IF geojson_files NOT EMPTY:
        geojson_path = MAX(geojson_files, BY modification_time)

    // --- Phase 4: Validierung ---
    disambiguation = _collect_disambiguation()

    result.success      = NOT has_error
    result.stdout       = stdout
    result.stderr       = stderr
    result.data_path    = data_path
    result.geojson_path = geojson_path
    result.summary_json = _load_summary_json(analysis_type, written_after=started_at)

    (is_valid, warnings) = _validate_summary_json(result.summary_json, analysis_type)

    IF NOT is_valid:
        result.success = false
        result.stderr += "\n" + JOIN(warnings, "\n")

    // --- Phase 5: Erklaerung ---
    IF is_valid:
        explanation = explain_de(question, stdout, stderr, model=model)
        result.explanation = STRING(explanation)
    ELSE:
        result.explanation = "Analyse fehlgeschlagen: " + warnings[0]

    RETURN (result, code, disambiguation)
END FUNCTION
```

---

## 5. resolve_terms -- Semantische Begriffsaufloesung

**Quelle:** `modules/disambiguator.py:151`

### Input
- `question`: Natuerlichsprachliche Frage (string)

### Output
- `ResolvedQuery`: Aufgeloeste Begriffe, Warnungen, Disambiguierungshinweise

### Algorithmus

```
FUNCTION resolve_terms(question):
    result  = NEW ResolvedQuery()
    q_lower = question.to_lowercase()

    // --- Phase 1: Deutsche Aliase aufloesen ---
    // TERM_ALIASES aus concepts.yml german_aliases
    // z.B. {"friedhof": ["graves"], "siedlung": ["settlement"]}
    FOR EACH (alias, targets) IN TERM_ALIASES:
        IF REGEX_MATCH("\b" + alias + "\b", q_lower):
            FOR EACH target IN targets:
                _resolve_single(target, result, confidence="alias", original=alias)

    // --- Phase 2: Bekannte Werte suchen ---
    // VALUE_INDEX: {wert_lowercase: [(Knotentyp, Property), ...]}
    FOR EACH (value, locations) IN VALUE_INDEX:
        IF LENGTH(value) < 3: CONTINUE  // Falsch-Positive vermeiden

        IF REGEX_MATCH("\b" + value + "\b", q_lower):
            // Bereits per Alias aufgeloest?
            already = ANY term IN result.terms
                      WHERE value IN term.resolved_values (lowercase)
            IF NOT already:
                _resolve_single(value, result, confidence="exact", original=value)

    // --- Phase 3: Indikator-Gruppen ---
    groups = ["grave_indicators", "mobility_indicators",
              "sedentary_indicators", "water_sources", "stone_indicators"]

    FOR EACH group_name IN groups:
        clean = group_name.replace("_", " ")
        IF clean IN q_lower OR group_name IN q_lower:
            group_data = concepts[group_name]

            IF IS_DICT(group_data):
                FOR EACH (node_type, values) IN group_data:
                    FOR EACH v IN values:
                        result.terms.APPEND(NEW ResolvedTerm(
                            original_text   = group_name,
                            node_type       = node_type.capitalize(),
                            property_name   = "Category",
                            resolved_values = [v],
                            confidence      = "group"
                        ))
            ELIF IS_LIST(group_data):
                FOR EACH v IN group_data:
                    result.terms.APPEND(NEW ResolvedTerm(
                        original_text   = group_name,
                        node_type       = "Feature",
                        property_name   = "Category",
                        resolved_values = [v],
                        confidence      = "group"
                    ))

    // Fuer UI-Sammlung in globalen Puffer pushen
    _disambiguation_results.APPEND(result)
    RETURN result
END FUNCTION


FUNCTION _resolve_single(value, result, confidence, original):
    locations = VALUE_INDEX.get(value.lowercase())
    IF locations IS EMPTY: RETURN

    unique = DEDUPLICATE(locations)

    IF LENGTH(unique) == 1:
        (node_type, prop) = unique[0]
        result.terms.APPEND(NEW ResolvedTerm(
            original_text=original, node_type=node_type,
            property_name=prop, resolved_values=[value],
            confidence=confidence
        ))
    ELSE:
        // Mehrdeutig -- beste Zuordnung waehlen
        (node_type, prop) = _pick_best_location(value, unique)
        result.terms.APPEND(NEW ResolvedTerm(...))
        others = unique EXCEPT (node_type, prop)
        result.disambiguation_notes.APPEND(
            "'{value}' aufgeloest als {node_type}.{prop} "
            "(auch in: {others})"
        )
END FUNCTION


FUNCTION _pick_best_location(value, locations):
    v = value.lowercase()

    // Prioritaet 1: Location-Begriffe → Location1
    IF v IN LOCATION_TERMS:
        RETURN FIRST (n, p) IN locations WHERE p == "Location1"

    // Prioritaet 2: Feature-exklusive Kategorie
    IF v IN FEATURE_ONLY_CATEGORIES: RETURN ("Feature", "Category")

    // Prioritaet 3: Site-exklusive Kategorie
    IF v IN SITE_ONLY_CATEGORIES: RETURN ("Site", "Category")

    // Prioritaet 4: Feature bevorzugen (granularer)
    FOR EACH (n, p) IN locations:
        IF n == "Feature" AND p == "Category": RETURN (n, p)

    RETURN locations[0]
END FUNCTION
```

---

## 6. _validate_summary_json -- Statistische Validierung

**Quelle:** `app/ui_chat.py:123`

### Input
- `summary`: Dict mit Statistik-Ergebnissen (oder None)
- `analysis_type`: Analysetyp (string)

### Output
- `(is_valid, warnings)`: Tupel aus Boolean und Liste von Warnungen

### Algorithmus

```
FUNCTION _validate_summary_json(summary, analysis_type):
    IF summary IS NULL:
        RETURN (true, [])

    warnings = []

    // Rekursive Pruefung (fuer verschachtelte Gruppen-Vergleiche)
    FUNCTION _check(d, prefix=""):
        FOR EACH (key, val) IN d:
            IF IS_DICT(val):
                _check(val, prefix="[{key}] ")
                CONTINUE

            k = key.to_lowercase()

            // Moran's I auf NaN pruefen
            IF k IN ("moran_i", "i"):
                IF IS_FLOAT(val) AND IS_NAN(val):
                    warnings.APPEND(
                        "{prefix}Moran's I ist NaN"
                        " -- konstante Variable, Analyse ungueltig"
                    )

            // p-Wert auf NaN pruefen
            IF k IN ("p_value", "p_sim"):
                IF IS_FLOAT(val) AND IS_NAN(val):
                    warnings.APPEND("{prefix}p-Wert ist NaN")

    _check(summary)
    RETURN (LENGTH(warnings) == 0, warnings)
END FUNCTION
```

**Anmerkung:** NaN-Werte in Moran's I entstehen typischerweise durch konstante Variablen
(alle Werte identisch), was raeumliche Autokorrelation bedeutungslos macht. Dies tritt
haeufig bei homogenen Teilmengen auf (z.B. alle Features derselben Kategorie bei
`group_label = 1`).

---

## 7. _run_normal_mode -- Haupt-Orchestrierungsschleife

**Quelle:** `app/ui_chat.py:294`

### Input
- `user_input`: Natuerlichsprachliche Nutzerfrage (string)
- `selected_model`: LLM-Modellname (string)

### Output
- `ChatMessage`: Vollstaendige Assistenten-Nachricht mit Schritten und Metriken

### Algorithmus

```
FUNCTION _run_normal_mode(user_input, selected_model):
    msg = NEW ChatMessage(role="assistant", timestamp=UTC_NOW())

    // Puffer leeren (neuer Request-Zyklus)
    drain_llm_results()
    drain_disambiguation_results()

    // Frage in Analyseschritte zerlegen
    plan = decompose_question(user_input, model=selected_model)

    IF plan.steps IS EMPTY:
        msg.text = "Keine gueltige Analyse erkannt."
        msg.metrics = _build_metrics(drain_llm_results())
        RETURN msg

    // Plan-Uebersicht in ChatMessage speichern
    msg.plan_steps = [{step_index, sub_question, analysis_type,
                       decision_type, depends_on, condition}
                      FOR EACH step IN plan.steps]

    // --- Schritt-Ausfuehrungs-Schleife ---
    FOR EACH step IN plan.steps:
        // Vorgaenger-Ergebnis laden (falls vorhanden)
        predecessor = plan.results[step.depends_on]
                      IF step.depends_on IS NOT NULL
                      ELSE NULL

        // Bedingung pruefen
        (should_run, reason) = evaluate_condition(step.condition, predecessor)

        IF NOT should_run:
            record = NEW StepRecord(skipped=true, skip_reason=reason, ...)
            msg.step_records.APPEND(record)
            plan.results[step.step_index] = NEW StepResult(
                skipped=true, skip_reason=reason, success=false
            )
            CONTINUE

        // Kontext aus Vorgaenger-Ergebnissen aufbauen
        prior_context = build_prior_context(plan, step)
        effective_question = step.sub_question
        IF prior_context IS NOT EMPTY:
            effective_question += "\n\nContext from prior analysis:\n" + prior_context

        data_path    = "results/analysis_input_step{step.step_index}.json"
        step_started = TIME_NOW()

        // Entscheidungstyp-Verzweigung
        IF step.decision_type == "cypher":
            (result, cypher_query, cypher_preview, disamb) =
                _execute_cypher_step(effective_question, selected_model)
            record = NEW StepRecord(
                decision_type="cypher",
                cypher_query=cypher_query,
                cypher_preview=cypher_preview, ...
            )

        ELIF step.decision_type == "python":
            (result, python_code, disamb) =
                _execute_python_step(effective_question, step.analysis_type,
                                     selected_model, data_path, step_started)
            record = NEW StepRecord(
                decision_type="python",
                python_code=python_code,
                geojson_path=result.geojson_path, ...
            )

        ELSE:
            LOG.warning("Unbekannter Entscheidungstyp: %s", step.decision_type)
            result = NEW StepResult(success=false)
            record = NEW StepRecord(stderr="Unbekannter Typ")

        // Ergebnisse speichern
        msg.step_records.APPEND(record)
        plan.results[step.step_index] = result
    END FOR

    // Metriken aggregieren
    msg.metrics = _build_metrics(drain_llm_results())
    RETURN msg
END FUNCTION
```

---

## 8. _run_comparison_mode -- Modellvergleichs-Orchestrierung

**Quelle:** `app/ui_chat.py:442`

### Input
- `user_input`: Nutzerfrage (string)
- `selected_models`: Liste von LLM-Modellnamen (list[string])

### Output
- `ChatMessage`: Vergleichsnachricht mit comparison_table und Metriken

### Algorithmus

```
FUNCTION _run_comparison_mode(user_input, selected_models):
    msg = NEW ChatMessage(role="assistant", is_comparison=true)

    // Puffer leeren
    drain_llm_results()
    drain_disambiguation_results()

    // Zerlegung nur einmal mit dem ersten Modell
    plan = decompose_question(user_input, model=selected_models[0])
    drain_llm_results()  // Zerlegungs-Metriken nicht pro Modell zaehlen

    IF plan.steps IS EMPTY:
        msg.text = "Keine gueltige Analyse erkannt."
        RETURN msg

    // Nur der erste Schritt wird verglichen
    step = plan.steps[0]
    msg.text = "Modellvergleich: {step.decision_type} / {step.analysis_type}"

    comparison_rows    = []
    all_llm_for_global = []

    // --- Pro-Modell-Schleife ---
    FOR EACH model_name IN selected_models:
        drain_llm_results()
        drain_disambiguation_results()

        step_started = TIME_NOW()

        IF step.decision_type == "cypher":
            (result, _, _, _) = _execute_cypher_step(step.sub_question, model_name)
        ELIF step.decision_type == "python":
            data_path = "results/comparison_{model_name}_input.json"
            (result, _, _) = _execute_python_step(
                step.sub_question, step.analysis_type,
                model_name, data_path, step_started
            )
        ELSE:
            result = NEW StepResult(success=false)

        // Pro-Modell-Metriken sammeln
        llm_results = drain_llm_results()
        all_llm_for_global.EXTEND(llm_results)
        metrics_dict = _aggregate_metrics_dict(llm_results)

        comparison_rows.APPEND({
            "model":         model_name,
            "success":       result.success,
            "analysis_type": step.analysis_type,
            "explanation":   result.explanation[0:500],
            "metrics":       metrics_dict
        })
    END FOR

    msg.comparison_table = comparison_rows
    msg.metrics          = _build_metrics(all_llm_for_global)

    // Persistieren nach results/comparisons/
    _persist_comparison(user_input, comparison_rows)

    RETURN msg
END FUNCTION
```

**Anmerkung:** Der Vergleichsmodus fuehrt die Fragezerlegung nur einmal durch (erstes Modell),
damit alle Modelle dieselbe Teilfrage beantworten. Die Zerlegungs-Token werden aus den
Pro-Modell-Metriken herausgehalten, um faire Vergleichbarkeit zu gewaehrleisten.

---

## 9. run_chat -- Einstiegspunkt der Chat-Oberflaeche

**Quelle:** `app/ui_chat.py:519`

### Algorithmus

```
FUNCTION run_chat():
    messages = session_state["chat_messages"]

    // Willkommensnachricht bei leerem Chat
    IF messages IS EMPTY:
        render_welcome()

    // Alle historischen Nachrichten rendern
    FOR EACH msg IN messages:
        render_chat_message(msg)

    // Neue Eingabe verarbeiten
    user_input = st.chat_input("Frage stellen ...")
    IF user_input IS NULL: RETURN

    // User-Nachricht speichern und sofort rendern
    user_msg = NEW ChatMessage(role="user", text=user_input)
    session_state["chat_messages"].APPEND(user_msg)
    RENDER user_msg

    // Modell-Einstellungen aus Sidebar lesen
    comparison_mode = session_state.get("comparison_toggle", false)
    selected_model  = session_state.get("active_model", "gpt-4.1")

    // Ausfuehrung
    IF comparison_mode:
        selected_models = session_state.get("comparison_models_selected", [])
        IF LENGTH(selected_models) < 2:
            SHOW_WARNING("Bitte mindestens 2 Modelle auswaehlen")
            RETURN
        assistant_msg = _run_comparison_mode(user_input, selected_models)
    ELSE:
        assistant_msg = _run_normal_mode(user_input, selected_model)

    // In History speichern
    session_state["chat_messages"].APPEND(assistant_msg)

    // Turn-Limit durchsetzen
    session_state["chat_messages"] =
        enforce_turn_limit(session_state["chat_messages"])

    // Session-Metriken aktualisieren
    _update_session_metrics(assistant_msg.metrics)

    // Rerun fuer konsistentes Rendering
    st.rerun()
END FUNCTION
```

---

## 10. call_llm_with_prompt -- Zentrale LLM-Aufruf-Pipeline

**Quelle:** `modules/helper.py:180`

### Input
- `function_name`: Name der aufrufenden Funktion (string)
- `question`: Nutzerfrage (string)
- `prompt`: Gerenderter System-Prompt (string)
- `temperature`: Temperatur (float, default 0.2)
- `model`: Modellname (string, optional)

### Output
- `LLMResult`: Antworttext mit Token-/Kosten-Metadaten

### Algorithmus

```
FUNCTION call_llm_with_prompt(function_name, question, prompt, temperature, model):
    effective_model = model OR DEFAULT_MODEL
    cfg = get_model_config(effective_model)  // aus config/models.yml
    is_reasoning = cfg.type == "reasoning"

    // Nachrichten nach Modelltyp aufbauen
    IF is_reasoning:
        // Reasoning-Modelle: System-Prompt als Developer-Message
        messages = [
            {role: "developer", content: prompt},
            {role: "user",      content: "Frage: " + question}
        ]
    ELSE:
        messages = [
            {role: "system", content: prompt},
            {role: "user",   content: "Frage: " + question}
        ]

    // API-Parameter
    kwargs = {model: effective_model, messages: messages}
    IF cfg.supports_temperature:
        kwargs.temperature = temperature

    // API-Aufruf mit Zeitmessung
    start    = TIME_NOW()
    response = OPENAI_CLIENT.chat.completions.create(**kwargs)
    duration = TIME_NOW() - start

    answer = response.choices[0].message.content.strip()

    // Token-Zaehler auslesen
    usage             = response.usage
    prompt_tokens     = usage.prompt_tokens
    completion_tokens = usage.completion_tokens
    reasoning_tokens  = usage.completion_tokens_details.reasoning_tokens OR 0

    // Kosten berechnen
    cost = _calculate_cost(effective_model,
                           prompt_tokens, completion_tokens, reasoning_tokens)

    // Metadaten zusammenstellen
    metadata = {
        model, model_type, prompt_tokens, completion_tokens,
        reasoning_tokens, total_tokens, cost_usd, duration_seconds
    }

    // Ergebnis loggen (JSON nach results/function_name/)
    log_result(function_name, question, prompt, response, cost, ...)

    // LLMResult erzeugen
    result = NEW LLMResult(answer=answer, metadata=metadata)

    // DUALER RETURN-MECHANISMUS:
    // 1. In Modul-Level-Puffer fuer spaetere Metrik-Aggregation
    _request_llm_results.APPEND(result)
    // 2. Direkt an den Aufrufer
    RETURN result
END FUNCTION
```

**Anmerkung:** Der duale Return-Mechanismus erlaubt es den `_execute_*`-Funktionen,
`LLMResult` direkt als String zu verwenden (`result.__str__()` liefert den Antworttext),
waehrend `_build_metrics()` spaeter ueber `drain_llm_results()` alle gesammelten
Metadaten aggregiert, ohne dass jede Funktion Metriken explizit weiterreichen muss.

---

## 11. _calculate_cost -- Token-Kostenberechnung

**Quelle:** `modules/helper.py:108`

### Input
- `model_name`: Modellname (string)
- `prompt_tokens`: Anzahl Prompt-Tokens (int)
- `completion_tokens`: Anzahl Completion-Tokens (int)
- `reasoning_tokens`: Anzahl Reasoning-Tokens (int, default 0)

### Output
- `cost`: USD-Kosten (float)

### Algorithmus

```
FUNCTION _calculate_cost(model_name, prompt_tokens, completion_tokens, reasoning_tokens):
    cfg = get_model_config(model_name)

    // OpenAI: completion_tokens enthaelt bereits reasoning_tokens
    // Aufspaltung um Doppelzaehlung zu vermeiden
    visible_completion = completion_tokens - reasoning_tokens

    cost  = (prompt_tokens / 1000) * cfg.cost_per_1k_prompt
    cost += (visible_completion / 1000) * cfg.cost_per_1k_completion

    IF reasoning_tokens > 0:
        reasoning_rate = cfg.cost_per_1k_reasoning OR cfg.cost_per_1k_completion
        cost += (reasoning_tokens / 1000) * reasoning_rate

    RETURN ROUND(cost, 6)
END FUNCTION
```

**Anmerkung:** Die Trennung von sichtbaren Completion-Tokens und Reasoning-Tokens ist
notwendig, da OpenAI bei Reasoning-Modellen (z.B. O3) die Reasoning-Tokens in
`completion_tokens` einrechnet, aber einen separaten (hoehereen) Tarif dafuer berechnet.

---

## 12. run_python_code -- Subprocess-Ausfuehrung mit Isolation

**Quelle:** `modules/helper.py:282`

### Input
- `raw_code`: LLM-generierter Python-Code (string)

### Output
- `(stdout, stderr)`: Ausgaben des Subprozesses

### Algorithmus

```
FUNCTION run_python_code(raw_code):
    // Bereinigung: Markdown-Code-Bloecke und Prosa entfernen
    script_code = _clean(raw_code)

    // Temporaeres Verzeichnis fuer Isolation
    WITH temp_dir = NEW TemporaryDirectory():
        tmp_file = temp_dir / "gpt_script.py"
        WRITE_FILE(tmp_file, script_code)

        // Subprocess mit Timeout starten
        proc = subprocess.run(
            command     = ["python", tmp_file],
            capture     = STDOUT + STDERR,
            text_mode   = true,
            timeout     = 900  // 15 Minuten
        )
    // TemporaryDirectory wird automatisch bereinigt

    RETURN (proc.stdout, proc.stderr)
END FUNCTION


FUNCTION _clean(code):
    // Markdown-Code-Bloecke entfernen
    code = REGEX_REPLACE("```.*?```", "", code, DOTALL)

    // Prosa vor der ersten Python-Direktive entfernen
    FOR EACH (index, line) IN ENUMERATE(code.lines()):
        IF line.starts_with("import ") OR "from " OR "def " OR "class ":
            RETURN code.lines()[index:]
    RETURN code.strip()
END FUNCTION
```

---

## 13. generate_cypher -- Cypher-Generierung mit Validierung

**Quelle:** `modules/llm.py:139`

### Input
- `question`: Nutzerfrage (string)
- `model`: LLM-Modellname (string, optional)

### Output
- `cypher`: Validierter Cypher-Query (string)

### Algorithmus

```
FUNCTION generate_cypher(question, model):
    // Begriffe aufloesen (pusht in Disambiguierungs-Puffer)
    resolved      = resolve_terms(question)
    resolved_text = format_resolved_terms(resolved)

    // Systemprompt aus Template generieren
    prompt = render_template("generate_cypher.jinja2", {
        "question":       question,
        "concepts":       concepts,        // aus concepts.yml
        "resolved_terms": resolved_text    // voraufgeloeste Begriffe
    })

    // LLM aufrufen
    raw = call_llm_with_prompt("generate_cypher", question, prompt, model=model)

    // JSON-Antwort parsen (erwartet {"cypher": "MATCH ..."})
    parsed = load_llm_json(raw)
    cypher = parsed.get("cypher")
    IF cypher IS NULL OR EMPTY:
        RAISE ValueError("Schluessel 'cypher' fehlt oder ist leer")

    // Plausibilitaetspruefung
    IF NOT cypher.strip().starts_with_case_insensitive("MATCH"):
        RAISE ValueError("Cypher beginnt nicht mit MATCH")

    // Werte validieren und ggf. automatisch korrigieren
    warnings = validate_cypher_values(cypher)
    IF warnings NOT EMPTY:
        LOG.warning("Cypher-Validierungswarnungen: %s", warnings)
        (cypher, corrections) = auto_correct_cypher(cypher)
        IF corrections NOT EMPTY:
            LOG.info("Automatisch korrigiert: %s", corrections)

    RETURN cypher
END FUNCTION
```

---

## 14. extract_relevant_data -- Datenextraktion fuer Python-Analysen

**Quelle:** `modules/llm.py:223`

### Input
- `question`: Nutzerfrage (string)
- `path`: Zielpfad fuer JSON-Ergebnis (string, default: "results/analysis_input.json")
- `model`: LLM-Modellname (string, optional)

### Output
- `rows`: Extrahierte Datenzeilen (list[dict])

### Nebenwirkung
- Speichert JSON-Datei unter `path`

### Algorithmus

```
FUNCTION extract_relevant_data(question, path, model):
    // 1. Aufruf von resolve_terms (erster Disambiguierungs-Aufruf pro Schritt)
    resolved      = resolve_terms(question)
    resolved_text = format_resolved_terms(resolved)

    // Prompt rendern
    prompt = render_template("extract_relevant_headers.jinja2", {
        "question":       question,
        "concepts":       concepts,
        "resolved_terms": resolved_text
    })

    // LLM aufrufen: generiert einen Cypher-Query fuer Datenextraktion
    raw = call_llm_with_prompt("extract_relevant_headers", question, prompt, model=model)

    // JSON parsen und Cypher extrahieren
    clauses = load_llm_json(raw)
    cypher  = clauses.get("cypher")
    IF cypher IS NULL OR EMPTY:
        RAISE ValueError("Schluessel 'cypher' fehlt")

    // Plausibilitaets- und Validierungspruefung
    IF NOT cypher.starts_with_case_insensitive("MATCH"):
        RAISE ValueError("Cypher beginnt nicht mit MATCH")

    warnings = validate_cypher_values(cypher)
    IF warnings NOT EMPTY:
        (cypher, corrections) = auto_correct_cypher(cypher)

    // Cypher gegen Neo4j ausfuehren
    rows = run_cypher(cypher)
    LOG.info("%d Zeilen extrahiert", LENGTH(rows))

    // Ergebnis als JSON speichern
    ENSURE_DIR(DIRNAME(path))
    WRITE_JSON(path, rows)

    RETURN rows
END FUNCTION
```

---

## 15. generate_analysis_code -- Python-Code-Generierung

**Quelle:** `modules/llm.py:73`

### Input
- `user_input`: Nutzerfrage (string)
- `analysis_type`: Analysetyp (string)
- `model`: LLM-Modellname (string, optional)
- `input_path`: Pfad zur Eingabe-JSON (string)

### Output
- `list[dict]`: Liste mit einem Record (timestamp, question, analysis_type, code)

### Algorithmus

```
FUNCTION generate_analysis_code(user_input, analysis_type, model, input_path):
    // Vorschau der Eingabedaten laden (erste 3 Zeilen)
    preview_json = ""
    IF FILE_EXISTS(input_path):
        TRY:
            rows = LOAD_JSON(input_path)
            preview_json = JSON.stringify(rows[0:3], pretty=true)
        CATCH: // Vorschau ist optional

    // 2. Aufruf von resolve_terms (zweiter Disambiguierungs-Aufruf pro Schritt)
    resolved      = resolve_terms(user_input)
    resolved_text = format_resolved_terms(resolved)

    // Prompt rendern (enthaelt analyse-typ-spezifische Methodik-Guidance)
    prompt = render_template("generate_analysis_code.jinja2", {
        "question":       user_input,
        "analysis_type":  analysis_type,
        "concepts":       concepts,
        "preview_json":   preview_json,
        "input_path":     input_path,
        "resolved_terms": resolved_text
    })

    // LLM aufrufen
    raw_answer = call_llm_with_prompt(
        "generate_analysis_code", user_input, prompt, model=model
    )

    // Code-Block extrahieren
    code_block = strip_code_fences(raw_answer).strip()

    // Record fuer Nachvollziehbarkeit
    RETURN [{
        "timestamp":     UTC_NOW(),
        "question":      user_input,
        "analysis_type": analysis_type,
        "code":          code_block
    }]
END FUNCTION
```

**Anmerkung:** Der doppelte `resolve_terms()`-Aufruf pro Python-Schritt (einmal in
`extract_relevant_data`, einmal hier) ist beabsichtigt -- beide LLM-Aufrufe benoetigen
die aufgeloesten Begriffe fuer praezise Prompts. Die resultierende Duplikation wird
spaeter durch `_collect_disambiguation()` bereinigt.

---

## 16. _merge_comparative_steps -- Vergleichsschritt-Zusammenfuehrung

**Quelle:** `modules/llm.py:297`

### Input
- `plan`: ChainPlan mit potenziell duplizierten Vergleichsschritten

### Output
- `plan`: ChainPlan mit zusammengefuehrten Schritten

### Algorithmus

```
FUNCTION _merge_comparative_steps(plan):
    // Nur bei Vergleichsfragen anwenden
    markers = ["bzw.", "beziehungsweise", "vs.", "versus",
               "im Vergleich zu", "verglichen mit", "compared to",
               "gegenueber"]

    IF NONE OF markers IN plan.original_question:
        RETURN plan  // Keine Vergleichsfrage

    // Unabhaengige Schritte identifizieren (kein depends_on, condition=NONE)
    independent = [s FOR s IN plan.steps
                   WHERE s.depends_on IS NULL AND s.condition == NONE]

    // Nach (analysis_type, decision_type) gruppieren
    key_counts = COUNTER((s.analysis_type, s.decision_type) FOR s IN independent)
    duplicated_keys = {key FOR (key, count) IN key_counts WHERE count > 1}

    IF duplicated_keys IS EMPTY:
        RETURN plan  // Keine Duplikate

    // Index-Mapping aufbauen: alte step_index -> neue step_index
    merged    = NEW ChainPlan(original_question=plan.original_question)
    index_map = {}
    seen_keys = {}

    FOR EACH step IN plan.steps:
        key = (step.analysis_type, step.decision_type)
        is_independent = step.depends_on IS NULL AND step.condition == NONE

        IF is_independent AND key IN duplicated_keys:
            IF key NOT IN seen_keys:
                // Ersten Step behalten, sub_question = Originalfrage
                seen_keys.ADD(key)
                new_idx = LENGTH(merged.steps) + 1
                index_map[step.step_index] = new_idx
                merged.steps.APPEND(NEW ChainStep(
                    step_index    = new_idx,
                    sub_question  = plan.original_question,
                    analysis_type = step.analysis_type,
                    decision_type = step.decision_type
                ))
            ELSE:
                // Duplikat -> auf bereits gemergten Step mappen
                merged_idx = FIND s.step_index IN merged.steps
                             WHERE (s.analysis_type, s.decision_type) == key
                index_map[step.step_index] = merged_idx
        ELSE:
            // Step normal uebernehmen, depends_on remappen
            new_idx = LENGTH(merged.steps) + 1
            index_map[step.step_index] = new_idx
            remapped_dep = index_map.get(step.depends_on) IF step.depends_on ELSE NULL
            merged.steps.APPEND(NEW ChainStep(
                step_index    = new_idx,
                sub_question  = step.sub_question,
                depends_on    = remapped_dep,
                condition     = step.condition,
                context_hint  = step.context_hint,
                ...
            ))
    END FOR

    RETURN merged
END FUNCTION
```

**Anmerkung:** Diese Funktion ist ein deterministischer Guard gegen ein LLM-Artefakt:
Bei Vergleichsfragen wie "Sind Graeber bei Cairns haeufiger als bei Settlements?"
kann das LLM die Frage in zwei identische Autokorrelationsschritte zerlegen, statt
einen einzelnen vergleichenden Schritt zu erzeugen. Der Merge korrigiert dies, indem
die `sub_question` auf die Originalfrage zurueckgesetzt wird.

---

## 17. _collect_disambiguation -- Disambiguierungs-Deduplizierung

**Quelle:** `app/ui_chat.py:55`

### Input
- (keine Parameter -- liest aus globalem Puffer)

### Output
- `DisambiguationRecord`: Deduplizierte Begriffe und Hinweise

### Algorithmus

```
FUNCTION _collect_disambiguation():
    // Globalen Puffer leeren (kann 1-2 ResolvedQuery-Objekte enthalten)
    all_resolved = drain_disambiguation_results()

    // Term-Deduplizierung via Composite-Key
    seen_terms = NEW SET()
    terms      = []

    FOR EACH rq IN all_resolved:
        FOR EACH t IN rq.terms:
            key = (t.original_text, t.node_type,
                   t.property_name, TUPLE(t.resolved_values))
            IF key IN seen_terms: CONTINUE
            seen_terms.ADD(key)
            terms.APPEND({
                original_text, node_type, property_name,
                resolved_values, confidence
            })

    // Notes-Deduplizierung
    seen_notes = NEW SET()
    notes      = []

    FOR EACH rq IN all_resolved:
        FOR EACH note IN rq.disambiguation_notes:
            IF note NOT IN seen_notes:
                seen_notes.ADD(note)
                notes.APPEND(note)

    RETURN NEW DisambiguationRecord(terms=terms, notes=notes)
END FUNCTION
```

**Anmerkung:** Die Deduplizierung ist notwendig, weil `resolve_terms()` pro
Python-Schritt zweimal aufgerufen wird -- einmal in `extract_relevant_data()` und
einmal in `generate_analysis_code()`. Ohne Deduplizierung wuerde dieselbe
Begriffsaufloesung doppelt in der UI erscheinen.

---

## 18. build_prior_context -- Kontext-Aufbau aus Vorgaenger-Ergebnissen

**Quelle:** `modules/chain.py:153`

### Input
- `plan`: ChainPlan mit bisherigen Ergebnissen
- `current_step`: Aktueller ChainStep

### Output
- `context`: Kontext-String fuer den LLM-Prompt (string)

### Algorithmus

```
FUNCTION build_prior_context(plan, current_step):
    IF current_step.depends_on IS NULL:
        RETURN ""

    predecessor = plan.results.get(current_step.depends_on)
    IF predecessor IS NULL OR NOT predecessor.success:
        RETURN ""

    lines = ["Vorherige Analyse (Schritt {predecessor.step_index}, "
             "{predecessor.analysis_type}):"]

    IF predecessor.explanation IS NOT EMPTY:
        lines.APPEND("  Ergebnis: " + predecessor.explanation[0:500])

    IF predecessor.summary_json IS NOT NULL:
        lines.APPEND("  Zusammenfassung: " + STRING(predecessor.summary_json))

    IF current_step.context_hint IS NOT EMPTY:
        lines.APPEND("  Hinweis: " + current_step.context_hint)

    RETURN JOIN(lines, "\n")
END FUNCTION
```

**Anmerkung:** Die Explanation wird auf 500 Zeichen begrenzt, um das Token-Budget
des nachfolgenden LLM-Aufrufs nicht uebermaezig zu belasten. Der `context_hint`
stammt aus der LLM-Zerlegung und kann z.B. "Verwende die p-Werte aus Schritt 1"
enthalten.

---

## 19. validate_cypher_values + auto_correct_cypher -- Cypher-Validierung und Fuzzy-Korrektur

**Quelle:** `modules/disambiguator.py:278` (validate_cypher_values), `:299` (auto_correct_cypher), `:320` (_find_closest_match)

### Input
- `cypher`: LLM-generierter Cypher-Query (string)

### Output
- `validate_cypher_values`: Liste von Warnungen
- `auto_correct_cypher`: (korrigierter_cypher, Liste von Korrekturen)

### Algorithmus

```
FUNCTION validate_cypher_values(cypher):
    warnings = []

    // Alle String-Literale aus dem Cypher extrahieren
    literals = REGEX_FIND_ALL("'([^']+)'", cypher)

    FOR EACH lit IN literals:
        lit_lower = lit.lowercase().strip()

        // Kurze/generische Werte ueberspringen
        IF lit_lower IS EMPTY OR lit_lower IN ("a", "b"): CONTINUE

        // Gegen bekannte Werte aus concepts.yml pruefen
        IF lit_lower NOT IN ALL_KNOWN_VALUES:
            suggestion = _find_closest_match(lit_lower)
            IF suggestion:
                warnings.APPEND("Unbekannt '{lit}' -- meinten Sie '{suggestion}'?")
            ELSE:
                warnings.APPEND("Unbekannt '{lit}' -- nicht in concepts.yml")

    RETURN warnings
END FUNCTION


FUNCTION auto_correct_cypher(cypher):
    corrections = []
    literals = REGEX_FIND_ALL("'([^']+)'", cypher)

    FOR EACH lit IN literals:
        lit_lower = lit.lowercase().strip()
        IF lit_lower IS EMPTY OR lit_lower IN ("a", "b"): CONTINUE

        IF lit_lower NOT IN ALL_KNOWN_VALUES:
            suggestion = _find_closest_match(lit_lower)
            IF suggestion:
                // In-Place-Ersetzung im Cypher-String
                cypher = cypher.REPLACE("'{lit}'", "'{suggestion}'")
                corrections.APPEND("'{lit}' -> '{suggestion}'")

    RETURN (cypher, corrections)
END FUNCTION


FUNCTION _find_closest_match(value, cutoff=0.7):
    // difflib.get_close_matches auf allen bekannten Werten
    matches = DIFFLIB_CLOSE_MATCHES(value, ALL_KNOWN_VALUES,
                                     n=1, cutoff=cutoff)
    IF matches NOT EMPTY:
        RETURN matches[0]
    RETURN NULL
END FUNCTION
```

**Anmerkung:** `ALL_KNOWN_VALUES` wird beim Modul-Import aus `concepts.yml` aufgebaut
und enthaelt alle bekannten Kategorien, Locations, Oberflaechen und RockArt-Motive
(ca. 70+ Werte). Der cutoff von 0.7 stellt sicher, dass nur hinreichend aehnliche
Werte korrigiert werden (z.B. "Graeves" -> "Graves", aber nicht "Water" -> "Wall").

---

## 20. _build_metrics -- Metrik-Aggregation

**Quelle:** `app/ui_chat.py:87`

### Input
- `llm_results`: Liste aller LLMResult-Objekte eines Request-Zyklus

### Output
- `MetricsRecord`: Aggregierte Token-Zaehler, Kosten, Dauer und verwendete Modelle

### Algorithmus

```
FUNCTION _build_metrics(llm_results):
    RETURN NEW MetricsRecord(
        prompt_tokens     = SUM(r.metadata.prompt_tokens FOR r IN llm_results),
        completion_tokens = SUM(r.metadata.completion_tokens FOR r IN llm_results),
        reasoning_tokens  = SUM(r.metadata.reasoning_tokens FOR r IN llm_results),
        total_tokens      = SUM(r.metadata.total_tokens FOR r IN llm_results),
        cost_usd          = ROUND(SUM(r.metadata.cost_usd FOR r IN llm_results), 6),
        duration_seconds  = ROUND(SUM(r.metadata.duration_seconds FOR r IN llm_results), 2),
        models_used       = UNIQUE(r.metadata.model FOR r IN llm_results)
    )
END FUNCTION
```

---

## 21. enforce_turn_limit -- Turn-Limit-Verwaltung

**Quelle:** `app/chat_models.py:107`

### Input
- `messages`: Liste aller ChatMessage-Objekte

### Output
- `messages`: Gekuerzte Liste (maximal MAX_TURNS * 2 Nachrichten)

### Algorithmus

```
CONSTANT MAX_TURNS = 50  // = 100 Nachrichten (User + Assistant)

FUNCTION enforce_turn_limit(messages):
    IF LENGTH(messages) <= MAX_TURNS * 2:
        RETURN messages

    // Aelteste Nachrichten entfernen (FIFO), neueste behalten
    RETURN messages[-(MAX_TURNS * 2):]
END FUNCTION
```

**Anmerkung:** Das Limit existiert als Speicherschutz fuer den Streamlit Session-State,
der nicht persistent ist und bei jedem Rerun im Arbeitsspeicher gehalten wird.
MAX_TURNS=50 erlaubt 50 Frage-Antwort-Paare, bevor die aeltesten Eintraege
verworfen werden.
