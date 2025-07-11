# 🏺 Archaeological Geo‑Analytics System (Wadi Abu Dom)

Dieses System ist das Kernanalysewerkzeug einer Masterarbeit zur semantisch-räumlichen Untersuchung archäologischer Fundstellen im Wadi Abu Dom, Sudan. Es kombiniert moderne Vektor- und Graphdatenbank-Technologien mit Large Language Models (LLMs), um komplexe geoarchäologische Fragestellungen interaktiv zu beantworten.

## 🚀 Quickstart

### 1. Setup mit Docker

```bash
git clone https://github.com/<dein-name>/wadi-analytics.git
cd wadi-analytics
cp .env.example .env
docker-compose up --build
```

Zugriff:

* Streamlit: [http://localhost:8501](http://localhost:8501)
* Neo4j: [http://localhost:7474](http://localhost:7474)

### 2. Import & Analyse

1. **Importieren:** Im Tab „Import“ die `.gpkg`-Datei einlesen und Embeddings erzeugen.
2. **Analysieren:** Tabs „Analyse“ oder „Chat“ verwenden.
3. **Visualisieren:** Hotspot-, Kolokations- oder Autokorrelationskarten interaktiv betrachten.

## 📂 Projektstruktur

```bash
├── app/
│   ├── main.py              # Streamlit-Startpunkt
│   ├── ui_chat.py           # Chat-Interface (LLM + Analyse)
│   ├── ui_import.py         # GPKG-Import & Fortschrittsanzeige
│   └── ui_map.py            # Visualisierung von GeoJSON-Analysen

├── modules/
│   ├── helper.py            # Templates, OpenAI-Calls, JSON-Helfer
│   ├── llm.py               # Analyse-Typ-Erkennung, Codegenerierung
│   ├── logger.py            # JSON-Logger mit Timestamp + Debug
│   ├── visualizations.py    # Geo-Darstellung mit Pydeck
│   └── neo4j/               # Graphdatenbank-Import & -Vorverarbeitung
│       ├── export_csv.py
│       ├── generate_embeddings.py
│       ├── gpkg_to_duckdb.py
│       └── neo4j_import.py

├── config/                  # YAMLs (Kategorie-Mapping, Prompt-Templates)
├── data/                    # Inputdaten (.gpkg, .csv, .parquet)
├── results/                 # Analyseergebnisse (.json, .geojson)
├── cache/                   # Zwischenstände (DuckDB, Embeddings)
├── templates/               # Jinja2 Templates (Code, Prompt, Visual)
├── logs/                    # app.log, debug.log, neo4j.log, ...
├── .env                     # API-Zugangsdaten & Pfade
└── docker-compose.yml       # Container für Streamlit + Neo4j
```

## 📊 Unterstützte Analysen

| Typ                 | Beispiel-Fragen                                     |
| ------------------- | --------------------------------------------------- |
| **Colocation**      | Nähe zwischen Tumuli und Siedlungen?                |
| **Autocorrelation** | Clusterbildung bei Features wie Brunnen oder Camps? |
| **Hotspot**         | Lokale Konzentrationen von Fundgruppen?             |
| **Correlation**     | Zusammenhang zw. Friedhofsgröße & Wohn-Indikatoren  |
| **Ripley’s K**      | Verteilungsmuster einzelner Fundkategorien          |
| **Distance**        | Distanzen zwischen Mobilitätsorten & Siedlungen     |


## ✅ Kontrollfragen & Ergebnisse

### 1. Kolokation Ridge-Tumuli ↔ Settlements

> Gibt es eine statistisch signifikante Kolokation von ridge tumulus-sites (`tumulus`, `ridge`) mit settlements (`habitation site`, `hut`, `settlement`)?
> Wenn ja, weisen diese eine räumliche Autokorrelation auf? Wo liegen Hotspots? Korreliert bei ridge tumuli die Anzahl von Features mit ihrem Kolokationswert?

🟡 **Kolokation messbar**, aber **nicht signifikant (p > 0.05)**
📍 Hotspot: **32°05'E bis 32°37'E**
📉 Kein signifikanter Zusammenhang mit Feature-Anzahl pro Site

### 2. Autokorrelation: Sesshaftigkeit & Mobilität

> Gibt es signifikante räumliche Autokorrelation bei Sesshaftigkeits- und Mobilitätsindikatoren?

✅ **Signifikante Autokorrelation**, vor allem zwischen **31°55'E und 32°05'E**
🌐 Teilweise nur in spezifischen Geländeabschnitten


### 3. Kolokation: Friedhofsgröße vs. Sesshaftigkeit

> Gibt es signifikante Kolokationen zwischen Sesshaftigkeits-/Mobilitätsindikatoren und Friedhofsgröße bzw. -abständen?

❌ **Mobilität**: Keine signifikante Kolokation
📉 **Sesshaftigkeit**: **Signifikant negative Kolokation** mit Friedhofsgröße & -abstand

### 4. Kolokation: Sesshaftigkeit ↔ Brunnen

> Gibt es eine signifikante Kolokation von Sesshaftigkeitsindikatoren mit Brunnen (`well`)?

✅ **Deutlich signifikant**
💧 Nähe zu Wasser korreliert mit Sesshaftigkeit

### 5. Kolokation: Rock Art ↔ Wohn- oder Mobilitätsindikatoren

> Gibt es Kolokation zwischen Rock Art und Sesshaftigkeits- oder Mobilitätsindikatoren?

✅ **Sesshaftigkeit**: Signifikant
❌ **Mobilität**: Keine signifikante Kolokation

## 📦 Technologien

| Komponente              | Details                                              |
| ----------------------- | ---------------------------------------------------- |
| **LLM**                 | OpenAI GPT-4o + text-embedding-3-small               |
| **Graphdatenbank**      | Neo4j 5.x mit GDS & APOC                             |
| **Räumliche Statistik** | PySAL, RipleyK, DBSCAN, Heatmaps                     |
| **UI**                  | Streamlit + Pydeck                                   |
| **CI**                  | JSON-Logs, modulares Logging, automatische Templates |
| **Caching**             | DuckDB + Parquet, Embedding-Memoisierung             |

## 📈 Evaluation

* 🔍 Alle Analyseergebnisse werden in `results/<funktion>/<timestamp>.json` gespeichert
* 🧪 Enthält: Frage, Prompt, Antwortvorschau, Modell, Code, Laufzeit, Ergebnisdaten

## 🛠 Hinweise

* Konfiguriere `.env` mit deinem OpenAI Key & Neo4j Zugang
* Visualisierungsergebnisse findest du unter `results/visualisierung/<type>/`
* Logs befinden sich in `logs/` (z. B. `debug.log`, `neo4j.log`, `app.log`)