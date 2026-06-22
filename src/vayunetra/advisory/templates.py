"""Advisory templates — pre-translated, no LLM in the live path.

Layout: 4 severity bands × 3 vulnerability tiers = 12 template slots per
language, populated for the 12 Indian languages plan §8.2 calls out. The
canonical English source lives in this module and acts as the fallback when a
translated file is missing. Translated variants live in
``data/templates/advisory_<lang>.json`` and are emitted by
``scripts/translate_templates.py`` (offline, IndicTrans2 on CPU).

Placeholders supported: ``{aqi}``, ``{pollutant}``, ``{forecast_change}``,
``{neighborhood}``. ``{aqi}`` is always rounded to int by the renderer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

Severity = Literal["good", "moderate", "poor", "severe"]
VulnTier = Literal["general", "elderly_children", "asthmatic"]

SEVERITIES: tuple[Severity, ...] = ("good", "moderate", "poor", "severe")
VULN_TIERS: tuple[VulnTier, ...] = ("general", "elderly_children", "asthmatic")

# 12 Indian languages — ISO 639-1 codes.
LANGUAGES: tuple[str, ...] = (
    "en", "hi", "bn", "mr", "te", "ta", "gu", "kn", "ml", "or", "pa", "as",
)

# Friendly display names (for UI dropdowns / Telegram keyboards).
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "हिन्दी",
    "bn": "বাংলা",
    "mr": "मराठी",
    "te": "తెలుగు",
    "ta": "தமிழ்",
    "gu": "ગુજરાતી",
    "kn": "ಕನ್ನಡ",
    "ml": "മലയാളം",
    "or": "ଓଡ଼ିଆ",
    "pa": "ਪੰਜਾਬੀ",
    "as": "অসমীয়া",
}


@dataclass(frozen=True)
class Template:
    headline: str
    body: str


# Canonical English source. Other languages are produced by translating *body*
# and *headline* — placeholders are preserved verbatim.
EN_TEMPLATES: dict[Severity, dict[VulnTier, Template]] = {
    "good": {
        "general": Template(
            "Air quality is good",
            "PM2.5 in {neighborhood} is {aqi} µg/m³, well within safe limits. "
            "Enjoy outdoor activities.",
        ),
        "elderly_children": Template(
            "Air quality is good for everyone",
            "PM2.5 is {aqi} µg/m³. Children and the elderly can exercise outdoors "
            "without restriction.",
        ),
        "asthmatic": Template(
            "Air is safe today",
            "PM2.5 is {aqi} µg/m³ — below the threshold that triggers asthma "
            "symptoms. Continue prescribed medication.",
        ),
    },
    "moderate": {
        "general": Template(
            "Air quality is moderate",
            "PM2.5 in {neighborhood} is {aqi} µg/m³. Sensitive groups should "
            "reduce prolonged outdoor exertion.",
        ),
        "elderly_children": Template(
            "Caution for children and elderly",
            "PM2.5 is {aqi} µg/m³. Limit children's outdoor play to under one "
            "hour. Elderly with heart conditions should stay indoors midday.",
        ),
        "asthmatic": Template(
            "Take asthma precautions",
            "PM2.5 is {aqi} µg/m³. Carry your rescue inhaler and avoid sustained "
            "outdoor exercise.",
        ),
    },
    "poor": {
        "general": Template(
            "Air quality is poor",
            "PM2.5 in {neighborhood} is {aqi} µg/m³. Reduce outdoor activity and "
            "wear an N95 mask if you must go out.",
        ),
        "elderly_children": Template(
            "Keep children and elderly indoors",
            "PM2.5 is {aqi} µg/m³. Cancel school sports. Elderly should remain "
            "indoors with windows shut.",
        ),
        "asthmatic": Template(
            "Asthmatics: avoid outdoors",
            "PM2.5 is {aqi} µg/m³. Stay indoors with windows closed; use a HEPA "
            "purifier in the room you sleep in.",
        ),
    },
    "severe": {
        "general": Template(
            "Severe air quality alert",
            "PM2.5 in {neighborhood} is {aqi} µg/m³. Stay indoors. Wear an N95 "
            "respirator for unavoidable trips.",
        ),
        "elderly_children": Template(
            "Severe alert — vulnerable groups indoors",
            "PM2.5 is {aqi} µg/m³. Children, the elderly and pregnant women must "
            "not go outside. Schools should suspend in-person classes.",
        ),
        "asthmatic": Template(
            "Severe alert — asthma emergency risk",
            "PM2.5 is {aqi} µg/m³. Stay indoors with a HEPA purifier. Keep your "
            "rescue inhaler within reach and seek medical care for shortness of "
            "breath.",
        ),
    },
}


def severity_for(aqi: float) -> Severity:
    """CPCB-aligned PM2.5 banding used across the project."""
    if aqi <= 60:
        return "good"
    if aqi <= 90:
        return "moderate"
    if aqi <= 120:
        return "poor"
    return "severe"


def _templates_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "templates"


@lru_cache(maxsize=32)
def load_language(lang: str) -> dict[Severity, dict[VulnTier, Template]]:
    """Return the template matrix for ``lang``, falling back to English."""
    if lang == "en" or lang not in LANGUAGES:
        return EN_TEMPLATES
    path = _templates_dir() / f"advisory_{lang}.json"
    if not path.exists():
        return EN_TEMPLATES
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[Severity, dict[VulnTier, Template]] = {}
    for sev in SEVERITIES:
        out[sev] = {}
        for tier in VULN_TIERS:
            node = raw.get(sev, {}).get(tier) or {}
            en = EN_TEMPLATES[sev][tier]
            out[sev][tier] = Template(
                headline=node.get("headline") or en.headline,
                body=node.get("body") or en.body,
            )
    return out


def render(
    lang: str,
    severity: Severity,
    vuln_tier: VulnTier,
    *,
    aqi: float,
    pollutant: str = "PM2.5",
    forecast_change: str = "",
    neighborhood: str = "your area",
) -> Template:
    """Format a template for delivery. Falls back to English on any miss."""
    matrix = load_language(lang)
    tpl = matrix.get(severity, EN_TEMPLATES[severity]).get(
        vuln_tier, EN_TEMPLATES[severity][vuln_tier]
    )
    fields = {
        "aqi": int(round(aqi)),
        "pollutant": pollutant,
        "forecast_change": forecast_change,
        "neighborhood": neighborhood,
    }
    return Template(
        headline=_safe_format(tpl.headline, fields),
        body=_safe_format(tpl.body, fields),
    )


def _safe_format(s: str, fields: dict[str, object]) -> str:
    try:
        return s.format(**fields)
    except (KeyError, IndexError):
        return s


def english_pairs() -> list[tuple[str, str, str]]:
    """Flat list of (severity, vuln_tier, english_text) — for the translator."""
    rows: list[tuple[str, str, str]] = []
    for sev in SEVERITIES:
        for tier in VULN_TIERS:
            t = EN_TEMPLATES[sev][tier]
            rows.append((sev, tier, t.headline))
            rows.append((sev, tier, t.body))
    return rows
