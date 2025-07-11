"""
Zentraler Logger für das gesamte Projekt
----------------------------------------
* File-Handler pro Log-Typ (app, debug, error, security, http, access, neo4j)
* Tages-Rotation für app-Log, sonst einfache FileHandler
* Einfache Nutzung:  ▸  from modules.logger import get_logger
"""

from __future__ import annotations

import logging
import logging.config
import os
import json
from datetime import datetime
from pathlib import Path
import logging
import json
import os

# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #
LOG_DIR: Path = Path(os.getenv("LOG_DIR", "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR = "results"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()
FORMAT = "[%(asctime)s] %(levelname)s — %(name)s: %(message)s"
logging.basicConfig(level=LOG_LEVEL, format=FORMAT)
BASE_FORMAT = "[%(asctime)s] %(levelname)s — %(name)s: %(message)s"

LOGGING_CFG: dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "std": {"format": BASE_FORMAT},
        "access": {"format": "%(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "std", "level": "INFO"},
        "app_file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "when": "midnight",
            "backupCount": 30,
            "formatter": "std",
            "filename": str(LOG_DIR / "app.log"),
            "encoding": "utf-8",
        },
        "debug_file": {
            "class": "logging.FileHandler",
            "formatter": "std",
            "level": "DEBUG",
            "filename": str(LOG_DIR / "debug.log"),
        },
        "error_file": {
            "class": "logging.FileHandler",
            "formatter": "std",
            "level": "ERROR",
            "filename": str(LOG_DIR / "error.log"),
        },
        "neo4j_file": {
            "class": "logging.FileHandler",
            "formatter": "std",
            "level": "INFO",
            "filename": str(LOG_DIR / "neo4j.log"),
        },
    },
    "loggers": {
        "": {  # root
            "handlers": ["console", "app_file", "error_file"],
            "level": "INFO",
            "propagate": False,
        },
        "debug": {"handlers": ["debug_file"], "level": "DEBUG", "propagate": False},
        "neo4j": {"handlers": ["neo4j_file"], "level": "INFO", "propagate": False},
    },
}

logging.config.dictConfig(LOGGING_CFG)


def get_logger(name: str) -> logging.Logger:  # noqa: D401
    """Return configured logger by *name*."""
    return logging.getLogger(name)


# Ordner, in dem Logs gespeichert werden
import os
import json
from datetime import datetime, timezone
import time

import os
import json
import time
from datetime import datetime, timezone

def log_result( 
    function_name: str,
    user_question: str,
    generated_prompt: str = None,
    result_data: list = None,
    llm_response: dict = None,
    code_generated: str = None,
    stdout: str = None,
    stderr: str = None,
    status: str = "success",
    results_dir: str = "results"
) -> str:
    """
    Logs the full result of a function call into a timestamped JSON file and a `latest.json` file.
    """
    target_dir = os.path.join(results_dir, function_name)
    os.makedirs(target_dir, exist_ok=True)

    # local timezone-safe timestamp
    timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"result_{function_name}_{timestamp}.json"
    filepath = os.path.join(target_dir, filename)

    start_time = llm_response.get("start_time") if isinstance(llm_response, dict) else None
    duration = None
    if start_time is not None:
        try:
            duration = round(time.time() - start_time, 2)
        except Exception:
            duration = None

    model_used = os.getenv("OPENAI_MODEL", "gpt-4o")

    result = {
        "timestamp": timestamp,
        "function": function_name,
        "question": user_question,
        "generated_prompt": generated_prompt,
        "model_used": model_used,
        "llm_response_raw": llm_response,
        "code_generated": code_generated,
        "result_preview": result_data[:10] if isinstance(result_data, list) else result_data,
        "stdout": stdout,
        "stderr": stderr,
        "status": status,
        "duration_seconds": duration,
    }

    # Save timestamped file
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # Save as latest.json (always overwritten)
    latest_path = os.path.join(results_dir, f"{function_name}.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    return filepath


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_json(logger: logging.Logger, level: str, label: str, obj: dict):
    msg = f"{label}:{json.dumps(obj, indent=2, ensure_ascii=False)}"
    getattr(logger, level.lower())(msg)
    
# Beispiel:
# logger = get_logger("debug")
# log_json(logger, "debug", "📦 Extrahierte Struktur", structure)