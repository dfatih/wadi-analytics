# 🏺 Archaeological Geo‑Analytics System (Wadi Abu Dom)

Dieses System ist das Kernanalysewerkzeug einer Masterarbeit zur semantisch-räumlichen Untersuchung archäologischer Fundstellen im Wadi Abu Dom, Sudan. Es kombiniert moderne Vektor- und Graphdatenbank-Technologien mit Large Language Models (LLMs), um komplexe geoarchäologische Fragestellungen interaktiv zu beantworten.

## ⚙️ Voraussetzungen

### 🔧 System

* **Betriebssystem**: Linux/macOS/Windows mit Docker-Unterstützung
* **RAM**: mindestens **8 GB empfohlen**
* **Python**: ≥ **3.10** (nur bei lokaler Installation relevant)

### 🐳 Docker-basierte Nutzung (empfohlen)

| Komponente         | Mindestversion    | Zweck                     |
| ------------------ | ----------------- | ------------------------- |
| **Docker Engine**  | `>= 20.10`        | Container-Orchestrierung  |
| **Docker Compose** | `v2.x` (kein v1!) | Streamlit + Neo4j starten |
| **Internetzugang** | erforderlich      | Embedding via OpenAI API  |


### ⚙️ Lokale Installation (optional, ohne Docker)

Wenn du ohne Docker arbeitest, stelle sicher, dass folgende Tools installiert sind:

| Tool              | Installationshinweis                                       |
| ----------------- | ---------------------------------------------------------- |
| **Python ≥ 3.10** | `sudo apt install python3.10`                              |
| **pip**           | `sudo apt install python3-pip`                             |
| **GeoPandas**     | `pip install geopandas` *(ggf. mit `gdal` Abhängigkeiten)* |
| **Neo4j Desktop** | [neo4j.com/download](https://neo4j.com/download/)          |

Außerdem:

```bash
pip install -r requirements.txt
cp .env.example .env
streamlit run app/main.py
```

---

### 🔑 API & Zugangsdaten

Die Datei `.env` muss folgende Einträge enthalten:

```env
OPENAI_API_KEY=sk-...
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=deinpasswort
GPKG_PATH=data/WADI_12_2016.gpkg
```


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

> Gibt es eine statistisch signifikante Kolokation von ridge tumulus-sites (category: tumulus; location1: ridge) mit settlements (categories: habitation site; hut; settlement)? Wenn ja, weisen diese eine räumliche Autokorrelation auf? Wo liegen die diesbezüglichen Hotspots? Korreliert bei ridge tumuli die Anzahl von Features pro Site mit ihrem Beitrag zur Kolokation mit habitation sites?

✅ Es gibt eine messbare Kolokation, diese liegt jedoch unterhalb der statistischen Signifkanzschwelle (p > 0.05). Hotspot ist die Region zwischen 32°05'E und 32°37'E.

### 2. Autokorrelation: Sesshaftigkeit & Mobilität

> Gibt es eine signifikante räumliche Autokorrelation von Sesshaftigkeitsindikatoren (categories: habitation site; hut; settlement) bzw. Mobilitätsindikatoren (shelter; stoneplace; camp site; fireplace; gravel platform)?

✅ Eine räumliche Autokorrelation beider Datensatzgruppen ist vor allem zwischen 31°55'E und 32°05'E messbar, aber nur in einigen Geländeabschnitten.


### 3. Kolokation: Friedhofsgröße vs. Sesshaftigkeit

> Gibt es statistisch signifikante Kolokationen von Sesshaftigkeits-  bzw. Mobilitätsindikatoren mit der Größe (Anzahl von Features) von Friedhöfen (categories: box graves; cleft burial; grave; dome grave; tumulus) und deren Abstand zu einander?

✅ Es gibt eine signifikante negative Kolokation zwischen der Friedhofsgröße und dem Vorhandensein von Sesshaftigkeitsindikatoren und ebenfalls eine negative Kolokation zwischen dem Abstand zwischen Friedhöfen und dem Vorhandensein von Sesshaftigkeitsindikatoren. Kolokationen beider Wertegruppen mit dem Vorhandensein von Mobilitätsindikatoren ist nicht signifikant.

### 4. Kolokation: Sesshaftigkeit ↔ Brunnen

> Gibt es eine statistisch signifikante Kolokation zwischen Sesshaftigkeitsindikatoren (categories: habitation site; hut; settlement) und Brunnen (category: well)?

✅ Es gibt eine deutlich signifkante Kolokation zwischen dem Vorhandensein von Sesshaftigkeitsindikatoren und Brunnen.

### 5. Kolokation: Rock Art ↔ Wohn- oder Mobilitätsindikatoren

> Gibt es eine statistisch signifikante Kolokation zwischen Mobilitäts- bzw. Sesshaftigkeitsindikatoren (categories: habitation site; hut; settlement) und der Feature-Category "rock art"?

✅ Es gibt eine signifikante Kolokation zwischen dem Vorhandensein von Sesshaftigkeitsindikatoren und der Kategorie "Rock Art". Zum Vorhandensein von Mobilitätsindikatoren ist die Kolokation nicht signifikant.

## 📦 Technologien

| Komponente              | Details                                              |
| ----------------------- | ---------------------------------------------------- |
| **LLM**                 | OpenAI GPT-4.1 + text-embedding-3-small              |
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