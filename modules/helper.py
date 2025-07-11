from __future__ import annotations
import os, re, json, math
from pathlib import Path
from typing import Optional
import yaml
import pandas as pd
from jinja2 import Environment, FileSystemLoader
from openai import OpenAI
from modules.logger import log_result
from neo4j import GraphDatabase
import csv
from typing import Any, List
import subprocess
import tempfile
from typing import Tuple

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4")
CLIENT_NAME     = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


DEFAULTS: dict = {
    "crs": "EPSG:4326",
    "distance_threshold": 1000,
    "n_simulations": 99,
    "value_column": "value",
    "output_file": "analysis_result.geojson",
}

TEMPLATE_FOLDER = Path(__file__).parent.parent / "templates"
CONFIG_FOLDER   = Path(__file__).parent.parent / "config"

env = Environment(
    loader=FileSystemLoader(TEMPLATE_FOLDER),
    trim_blocks=True,
    lstrip_blocks=True
)


def load_llm_json(raw: str) -> dict:
    """Strip ``` fences and parse JSON, raise with full raw output if broken."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.I).strip()
    return json.loads(cleaned)          # will raise JSONDecodeError if invalid


def render_template(name: str, context: dict, folder: str = "") -> str:
    if folder:
        path = TEMPLATE_FOLDER / folder / name
    else:
        path = TEMPLATE_FOLDER / name
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return env.get_template(str(path.relative_to(TEMPLATE_FOLDER))).render(**context)

def strip_code_fences(txt: str) -> str:
    """entfernt ```json …``` bzw. ``` … ``` Hüllen"""
    return re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.I).strip()

def sanitize_cypher_code(cypher: str) -> str:
    """ Entfernt Markdown-Wrapper wie ```cypher ... ``` aus dem LLM-Ausgabequery """
    cypher = cypher.strip()
    if cypher.startswith("```cypher"):
        cypher = cypher[len("```cypher"):].strip()
    if cypher.endswith("```"):
        cypher = cypher[:-3].strip()
    return cypher

def load_yaml(name: str) -> dict:
    path = CONFIG_FOLDER / name
    if not path.exists():
        raise FileNotFoundError(f"YAML config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)
    
def call_llm_with_prompt(
    function_name: str,
    question: str,
    prompt: str,
    preview: str,
    result_data=None,
    temperature: float = 0.2,
    model: Optional[str] = None,
) -> str:
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"Frage: {question}"},
        {"role": "assistant", "content": preview},
    ]

    response = CLIENT_NAME.chat.completions.create(
        model=model or MODEL_NAME,
        messages=messages,
        temperature=temperature,
    )

    final_answer = response.choices[0].message.content.strip()

    log_result(
        function_name=function_name,
        user_question=question,
        generated_prompt=prompt,
        result_data=result_data or [],
        llm_response=response.model_dump(),
        code_generated=final_answer,
        status="success",
        results_dir="results"
    )

    return final_answer

def load_llm_json(raw: str) -> dict:
    """
    Strip ``` / ```json fences and parse JSON.
    Raises JSONDecodeError with full raw output logged.
    """
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.I).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error(f"LLM JSON-parse error: {e}\nRaw LLM output:\n{raw}")
        raise


def _clean(code: str) -> str:
    """
    1. Entfernt alle ```-Blöcke (Markdown).
    2. Droppt Prosa vor der ersten Python-Direktive.
    """
    code = re.sub(r"```.*?```", "", code, flags=re.S)
    for i, line in enumerate(code.splitlines()):
        if line.lstrip().startswith(("import ", "from ", "def ", "class ")):
            return "\n".join(code.splitlines()[i:])
    return code.strip()


def run_python_code(raw_code: str) -> Tuple[str, str]:
    """Returns (stdout, stderr) of executed script."""
    script_code = _clean(raw_code)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "gpt_script.py"
        tmp.write_text(script_code, encoding="utf-8")

        proc = subprocess.run(
            ["python", str(tmp)],
            capture_output=True,
            text=True,
            timeout=900,
        )
    return proc.stdout, proc.stderr



def load_prompt(name: str) -> dict:
    """
    Lade ein klassisches statisches YML-Promptformat aus config/ (z. B. für explain_de)
    """
    path = CONFIG_FOLDER / f"{name}.yml"
    if not path.exists():
        raise FileNotFoundError(f"Prompt config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

_driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD"))
)


def run_cypher(query: str) -> List[dict[str, Any]]:
    with _driver.session() as session:
        return [rec.data() for rec in session.run(query)]