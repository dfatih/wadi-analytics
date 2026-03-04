"""Semantische Begriffsaufloesung und Cypher-Validierung fuer archaeologische Abfragen.

Loest Nutzerbegriffe (z.B. 'tumulus', 'ridge', 'Friedhof') in den korrekten
Neo4j-Knotentyp, Property-Namen und kanonische Werte auf.
Grundlage: config/concepts.yml.
"""
from __future__ import annotations

import difflib
import re
import threading
from dataclasses import dataclass, field
from typing import Optional

from modules.helper import load_yaml
from modules.logger import get_logger

logger = get_logger("debug")

concepts = load_yaml("concepts.yml")


# ---------------------------------------------------------------------------
# Umlaut-Normalisierung (ö->oe, ä->ae, ü->ue, ß->ss)
# ---------------------------------------------------------------------------
_UMLAUT_MAP = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
})


def _normalize_umlauts(text: str) -> str:
    """Normalisiert deutsche Umlaute zu ASCII-Aequivalenten."""
    return text.translate(_UMLAUT_MAP)


# ---------------------------------------------------------------------------
# Datenklassen
# ---------------------------------------------------------------------------
@dataclass
class ResolvedTerm:
    original_text: str
    node_type: str           # "Feature" | "Site"
    property_name: str       # "Category" | "Location1" | "RockArtMotif" | ...
    resolved_values: list[str]
    confidence: str          # "exact" | "alias" | "group" | "fuzzy"


@dataclass
class ResolvedQuery:
    terms: list[ResolvedTerm] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    disambiguation_notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Deutsche Aliase (aus concepts.yml, zentrale Quelle)
# ---------------------------------------------------------------------------
def _build_term_aliases(concepts: dict) -> dict[str, list[str]]:
    """Baut das Alias-Dict aus dem german_aliases-Abschnitt der concepts.yml.

    YAML-Werte koennen ein einzelner String oder eine Liste sein.
    Keys werden umlaut-normalisiert (ö->oe), damit sowohl Unicode- als auch
    ASCII-Eingaben matchen.
    """
    raw = concepts.get("german_aliases", {})
    aliases: dict[str, list[str]] = {}
    for alias, targets in raw.items():
        key = _normalize_umlauts(alias.lower())
        tgt = targets if isinstance(targets, list) else [targets]
        aliases[key] = tgt
    return aliases


TERM_ALIASES: dict[str, list[str]] = _build_term_aliases(concepts)


# ---------------------------------------------------------------------------
# Rueckwaerts-Lookup (einmalig beim Import gebaut)
# ---------------------------------------------------------------------------
def _build_value_index(concepts: dict) -> dict[str, list[tuple[str, str]]]:
    """Bildet jeden Wert (lowercase) auf eine Liste von (Knotentyp, Property) ab."""
    idx: dict[str, list[tuple[str, str]]] = {}

    def _add(val: str, node_type: str, prop: str) -> None:
        idx.setdefault(val.lower().strip(), []).append((node_type, prop))

    # Category-Maps
    for cat in concepts.get("category_map", {}).get("feature", []):
        _add(cat, "Feature", "Category")
    for cat in concepts.get("category_map", {}).get("site", []):
        _add(cat, "Site", "Category")

    # Category2-Maps
    for cat in concepts.get("category2_map", {}).get("feature", []):
        _add(cat, "Feature", "Category2")

    # Location1-Maps
    for loc in concepts.get("location1_map", {}).get("feature", []):
        _add(loc, "Feature", "Location1")
    for loc in concepts.get("location1_map", {}).get("site", []):
        _add(loc, "Site", "Location1")

    # Location2-Maps
    for loc in concepts.get("location2_map", {}).get("feature", []):
        _add(loc, "Feature", "Location2")
    for loc in concepts.get("location2_map", {}).get("site", []):
        _add(loc, "Site", "Location2")

    # Oberflaechentypen
    for suf in concepts.get("surface_types", {}).get("site", []):
        _add(suf, "Site", "Surface")

    # Condition-Werte
    for cond in concepts.get("condition_values", {}).get("feature", []):
        _add(cond, "Feature", "Condition")

    # RockArt-Motive (aufgeloest ueber HAS_ROCKART-Beziehung)
    for motif in concepts.get("rockart_motifs", []):
        _add(motif, "Feature", "RockArtMotif")

    # Daten-Tippfehler aus data_corrections ebenfalls indexieren
    for canonical, typos in concepts.get("data_corrections", {}).items():
        canon_lower = canonical.lower().strip()
        if canon_lower in idx:
            for typo in typos:
                for node_type, prop in idx[canon_lower]:
                    _add(typo, node_type, prop)

    return idx


def _build_all_values(concepts: dict) -> set[str]:
    """Sammelt alle bekannten Werte fuer die Tippfehler-Erkennung."""
    vals: set[str] = set()
    for section in ["category_map", "category2_map", "location1_map", "location2_map",
                     "condition_values"]:
        mapping = concepts.get(section, {})
        for node_values in mapping.values():
            if isinstance(node_values, list):
                vals.update(v.lower() for v in node_values)
    for suf in concepts.get("surface_types", {}).get("site", []):
        vals.add(suf.lower())
    for motif in concepts.get("rockart_motifs", []):
        vals.add(motif.lower())
    # Daten-Tippfehler als bekannte Werte registrieren
    for canonical, typos in concepts.get("data_corrections", {}).items():
        vals.add(canonical.lower())
        for typo in typos:
            vals.add(typo.lower())
    return vals


VALUE_INDEX = _build_value_index(concepts)
ALL_KNOWN_VALUES = _build_all_values(concepts)

# Direkte Tippfehler->Kanonisch-Map (fuer auto_correct_cypher)
_DATA_CORRECTIONS_MAP: dict[str, str] = {}
for _canonical, _typos in concepts.get("data_corrections", {}).items():
    for _typo in _typos:
        _DATA_CORRECTIONS_MAP[_typo.lower()] = _canonical


# ---------------------------------------------------------------------------
# Disambiguierungs-Sammler (thread-safe, gleich wie LLM-Ergebnis-Sammler)
# ---------------------------------------------------------------------------
_thread_local = threading.local()


def _get_disambiguation_buffer() -> list[ResolvedQuery]:
    """Gibt den thread-lokalen Disambiguierungs-Puffer zurueck."""
    if not hasattr(_thread_local, "disambiguation_results"):
        _thread_local.disambiguation_results = []
    return _thread_local.disambiguation_results


def drain_disambiguation_results() -> list[ResolvedQuery]:
    """Gibt alle gesammelten ResolvedQuery-Objekte zurueck und leert den Puffer."""
    buf = _get_disambiguation_buffer()
    results = list(buf)
    buf.clear()
    return results

# Feature-/Site-exklusive Kategorien (aus concepts.yml disambiguation_rules)
_rules = concepts.get("disambiguation_rules", {})
FEATURE_ONLY_CATEGORIES = {c.lower() for c in _rules.get("feature_only_categories", [])}
SITE_ONLY_CATEGORIES = {c.lower() for c in _rules.get("site_only_categories", [])}

# Location-Begriffe (sind nie Kategorien)
_location_terms = set()
for loc_list in concepts.get("location_terms", {}).values():
    _location_terms.update(v.lower() for v in loc_list)
LOCATION_TERMS = _location_terms


# ---------------------------------------------------------------------------
# Kern: Begriffsaufloesung
# ---------------------------------------------------------------------------
def resolve_terms(question: str) -> ResolvedQuery:
    """Extrahiert und loest archaeologische Begriffe aus einer Nutzerfrage auf."""
    result = ResolvedQuery()
    q_lower = question.lower()
    q_normalized = _normalize_umlauts(q_lower)

    # 1. Alias-Aufloesung (Deutsch -> Englisch, umlaut-normalisiert)
    for alias, targets in TERM_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", q_normalized):
            for target in targets:
                _resolve_single(target, result, confidence="alias", original=alias)

    # 2. Bekannte Werte in der Frage suchen (Wortgrenzen-Matching)
    for value, locations in VALUE_INDEX.items():
        # Sehr kurze Werte ueberspringen (Falsch-Positive vermeiden)
        if len(value) < 3:
            continue
        if re.search(r"\b" + re.escape(value) + r"\b", q_lower):
            # Pruefen ob schon per Alias aufgeloest
            already = any(
                value in [v.lower() for v in t.resolved_values]
                for t in result.terms
            )
            if not already:
                _resolve_single(value, result, confidence="exact", original=value)

    # 3. Indikator-Gruppen pruefen (Gruppenname in der Frage erwaehnt)
    for group_name in ["grave_indicators", "mobility_indicators", "sedentary_indicators",
                       "water_sources", "stone_indicators"]:
        group_data = concepts.get(group_name, {})
        clean_name = group_name.replace("_", " ")
        if clean_name in q_lower or group_name in q_lower:
            if isinstance(group_data, dict):
                for node_type, values in group_data.items():
                    for v in values:
                        result.terms.append(ResolvedTerm(
                            original_text=group_name,
                            node_type=node_type.capitalize(),
                            property_name="Category",
                            resolved_values=[v],
                            confidence="group",
                        ))
            elif isinstance(group_data, list):
                for v in group_data:
                    result.terms.append(ResolvedTerm(
                        original_text=group_name,
                        node_type="Feature",
                        property_name="Category",
                        resolved_values=[v],
                        confidence="group",
                    ))

    # Fuer UI-Anzeige sammeln
    _get_disambiguation_buffer().append(result)
    return result


def _resolve_single(value: str, result: ResolvedQuery, *, confidence: str, original: str) -> None:
    """Loest einen einzelnen Wert auf und fuegt ihn mit Disambiguierungslogik zum Ergebnis hinzu."""
    locations = VALUE_INDEX.get(value.lower(), [])
    if not locations:
        return

    # Duplikate entfernen
    unique_locations = list(set(locations))

    if len(unique_locations) == 1:
        node_type, prop = unique_locations[0]
        result.terms.append(ResolvedTerm(
            original_text=original,
            node_type=node_type,
            property_name=prop,
            resolved_values=[value],
            confidence=confidence,
        ))
    else:
        # Mehrere moegliche Zuordnungen -- disambiguieren
        node_type, prop = _pick_best_location(value, unique_locations)
        result.terms.append(ResolvedTerm(
            original_text=original,
            node_type=node_type,
            property_name=prop,
            resolved_values=[value],
            confidence=confidence,
        ))
        others = [(n, p) for n, p in unique_locations if (n, p) != (node_type, prop)]
        result.disambiguation_notes.append(
            f"'{value}' aufgeloest als {node_type}.{prop} "
            f"(auch vorhanden in: {', '.join(f'{n}.{p}' for n, p in others)})"
        )


def _pick_best_location(value: str, locations: list[tuple[str, str]]) -> tuple[str, str]:
    """Waehlt die beste (Knotentyp, Property)-Zuordnung fuer einen mehrdeutigen Wert.

    Prioritaet:
    1. Bekannter Location-Begriff -> Location1
    2. Feature-exklusive Kategorie -> Feature.Category
    3. Site-exklusive Kategorie -> Site.Category
    4. Standard -> Feature (granularer Knoten)
    """
    v_lower = value.lower()

    # Location-Begriffe sind nie Kategorien
    if v_lower in LOCATION_TERMS:
        for node_type, prop in locations:
            if prop == "Location1":
                return node_type, prop

    # Feature-exklusive Kategorie
    if v_lower in FEATURE_ONLY_CATEGORIES:
        return "Feature", "Category"

    # Site-exklusive Kategorie
    if v_lower in SITE_ONLY_CATEGORIES:
        return "Site", "Category"

    # Standard: Feature bevorzugen (spezifischer)
    for node_type, prop in locations:
        if node_type == "Feature" and prop == "Category":
            return node_type, prop

    return locations[0]


# ---------------------------------------------------------------------------
# Cypher-Validierung
# ---------------------------------------------------------------------------
def validate_cypher_values(cypher: str) -> list[str]:
    """Prueft String-Literale in einem Cypher-Query gegen bekannte Werte.

    Gibt eine Liste von Warnungen fuer unbekannte Werte zurueck.
    """
    warnings: list[str] = []
    literals = re.findall(r"'([^']+)'", cypher)

    for lit in literals:
        lit_lower = lit.lower().strip()
        if not lit_lower or lit_lower in ("a", "b"):
            continue
        if lit_lower not in ALL_KNOWN_VALUES:
            suggestion = _find_closest_match(lit_lower)
            if suggestion:
                warnings.append(f"Unbekannter Wert '{lit}' im Cypher -- meinten Sie '{suggestion}'?")
            else:
                warnings.append(f"Unbekannter Wert '{lit}' im Cypher -- nicht in concepts.yml gefunden")
    return warnings


def auto_correct_cypher(cypher: str) -> tuple[str, list[str]]:
    """Versucht unbekannte String-Literale im Cypher per Fuzzy-Matching zu korrigieren.

    Prueft zuerst explizite Daten-Tippfehler (data_corrections), dann Fuzzy-Matching.
    Gibt (korrigierter_cypher, liste_der_korrekturen) zurueck.
    """
    corrections: list[str] = []
    literals = re.findall(r"'([^']+)'", cypher)

    for lit in literals:
        lit_lower = lit.lower().strip()
        if not lit_lower or lit_lower in ("a", "b"):
            continue
        if lit_lower not in ALL_KNOWN_VALUES:
            # Explizite Tippfehler-Korrektur aus data_corrections (hohe Prioritaet)
            if lit_lower in _DATA_CORRECTIONS_MAP:
                corrected = _DATA_CORRECTIONS_MAP[lit_lower]
                cypher = cypher.replace(f"'{lit}'", f"'{corrected}'")
                corrections.append(f"'{lit}' -> '{corrected}' (Datenfehler)")
            else:
                suggestion = _find_closest_match(lit_lower)
                if suggestion:
                    cypher = cypher.replace(f"'{lit}'", f"'{suggestion}'")
                    corrections.append(f"'{lit}' -> '{suggestion}'")

    return cypher, corrections


def _find_closest_match(value: str, cutoff: float = 0.7) -> Optional[str]:
    """Findet den aehnlichsten bekannten Wert per difflib."""
    matches = difflib.get_close_matches(value, ALL_KNOWN_VALUES, n=1, cutoff=cutoff)
    return matches[0] if matches else None


def fix_cypher_syntax(cypher: str) -> tuple[str, list[str]]:
    """Behebt veraltete Cypher-Syntax (Neo4j 5.x Kompatibilitaet).

    Ersetzt ``exists(var.prop)`` durch ``var.prop IS NOT NULL``.
    Gibt (korrigierter_cypher, liste_der_korrekturen) zurueck.
    """
    corrections: list[str] = []
    pattern = re.compile(r"\bexists\s*\(\s*([\w]+\.[\w]+)\s*\)")
    matches = pattern.findall(cypher)
    if matches:
        cypher = pattern.sub(r"\1 IS NOT NULL", cypher)
        corrections.append(f"exists() -> IS NOT NULL ({len(matches)}x)")
        logger.info("Cypher-Syntax korrigiert: %s", corrections)
    return cypher, corrections


# ---------------------------------------------------------------------------
# Formatierung fuer Template-Injection
# ---------------------------------------------------------------------------
def format_resolved_terms(resolved: ResolvedQuery) -> str:
    """Formatiert aufgeloeste Begriffe als Textblock fuer die Template-Einbindung."""
    if not resolved.terms:
        return ""

    lines = ["PRE-RESOLVED TERMS (use these mappings, do NOT override):"]
    for t in resolved.terms:
        vals = ", ".join(f"'{v}'" for v in t.resolved_values)
        if t.property_name == "RockArtMotif":
            lines.append(f"  - '{t.original_text}' -> Feature via [:HAS_ROCKART]->(:RockArtMotif) "
                         f"name IN [{vals}] ({t.confidence})")
        else:
            lines.append(f"  - '{t.original_text}' -> {t.node_type}.{t.property_name} IN [{vals}] ({t.confidence})")

    if resolved.disambiguation_notes:
        lines.append("")
        lines.append("DISAMBIGUATION NOTES:")
        for note in resolved.disambiguation_notes:
            lines.append(f"  - {note}")

    return "\n".join(lines)
