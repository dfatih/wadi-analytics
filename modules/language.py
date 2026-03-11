"""Spracherkennung fuer Benutzerfragen.

Erkennt die Sprache via langdetect und liefert den ISO-639-1-Code
sowie den englischen Sprachnamen. Kein Streamlit -- verwendbar in
jedem Kontext (Agent Loop, Scripts, UI).
"""
from __future__ import annotations

from langdetect import detect as _langdetect

# ---------------------------------------------------------------------------
# Sprachcode -> Vollname (fuer LLM-Instruktion im Systemprompt)
# ---------------------------------------------------------------------------
_LANG_NAMES: dict[str, str] = {
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "pl": "Polish",
    "ru": "Russian",
    "ar": "Arabic",
    "zh-cn": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "tr": "Turkish",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "cs": "Czech",
    "uk": "Ukrainian",
}


def detect_language(text: str) -> str:
    """Erkennt die Sprache eines Textes. Gibt ISO-639-1-Code zurueck (z.B. 'en', 'de', 'es')."""
    try:
        return _langdetect(text)
    except Exception:
        return "de"


def language_name(code: str) -> str:
    """Gibt den englischen Sprachnamen fuer einen ISO-Code zurueck."""
    return _LANG_NAMES.get(code, code.capitalize())
