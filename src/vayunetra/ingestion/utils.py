"""Small shared helpers for ingestion flows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


_CONF_ROOT = Path(__file__).resolve().parents[3] / "conf"


def load_city_config(city: str) -> dict[str, Any]:
    f = _CONF_ROOT / "city" / f"{city}.yaml"
    if not f.exists():
        raise FileNotFoundError(f"city config not found: {f}")
    return yaml.safe_load(f.read_text())


def city_bbox(city: str) -> tuple[float, float, float, float]:
    """Returns (minLon, minLat, maxLon, maxLat)."""
    cfg = load_city_config(city)
    return tuple(cfg["bbox"])  # type: ignore[return-value]


# OpenAQ parameter-id ↔ name. v3 sometimes returns just an id.
_PARAM_ID_TO_NAME = {
    1: "pm10",
    2: "pm25",
    3: "o3",
    4: "co",
    5: "no2",
    6: "so2",
}


def pollutant_from_id(pid: int | None) -> str | None:
    return _PARAM_ID_TO_NAME.get(pid) if pid is not None else None


def parameter_id_for(name: str) -> int | None:
    for pid, n in _PARAM_ID_TO_NAME.items():
        if n == name:
            return pid
    return None
