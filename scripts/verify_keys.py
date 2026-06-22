"""Ping every external API VayuNetra depends on and print OK/FAIL.

Run with: `poetry run python scripts/verify_keys.py`
Exit code: 0 if all required keys work, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from vayunetra.common.config import get_settings


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


async def check_openaq(s, client: httpx.AsyncClient) -> CheckResult:
    if not s.openaq_api_key:
        return CheckResult("openaq", False, "OPENAQ_API_KEY not set")
    r = await client.get(
        "https://api.openaq.org/v3/locations",
        params={"limit": 1},
        headers={"X-API-Key": s.openaq_api_key},
        timeout=10,
    )
    return CheckResult("openaq", r.status_code == 200, f"HTTP {r.status_code}")


async def check_open_meteo(s, client: httpx.AsyncClient) -> CheckResult:
    r = await client.get(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": 28.6, "longitude": 77.2, "hourly": "temperature_2m"},
        timeout=10,
    )
    return CheckResult("open_meteo", r.status_code == 200, f"HTTP {r.status_code}")


async def check_firms(s, client: httpx.AsyncClient) -> CheckResult:
    if not s.firms_api_key:
        return CheckResult("firms", False, "FIRMS_API_KEY not set")
    # FIRMS data_availability endpoint is the cheapest call.
    r = await client.get(
        f"https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{s.firms_api_key}/all",
        timeout=10,
    )
    return CheckResult("firms", r.status_code == 200, f"HTTP {r.status_code}")


async def check_groq(s, client: httpx.AsyncClient) -> CheckResult:
    if not s.groq_api_key:
        return CheckResult("groq", False, "GROQ_API_KEY not set (fallback only)")
    r = await client.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {s.groq_api_key}"},
        timeout=10,
    )
    return CheckResult("groq", r.status_code == 200, f"HTTP {r.status_code}")


async def check_ollama(s, client: httpx.AsyncClient) -> CheckResult:
    try:
        r = await client.get(f"{s.ollama_base_url}/api/tags", timeout=5)
        return CheckResult("ollama", r.status_code == 200, f"HTTP {r.status_code}")
    except Exception as e:
        return CheckResult("ollama", False, f"unreachable: {e}")


async def check_telegram(s, client: httpx.AsyncClient) -> CheckResult:
    if not s.telegram_token:
        return CheckResult("telegram", False, "TELEGRAM_TOKEN not set")
    r = await client.get(f"https://api.telegram.org/bot{s.telegram_token}/getMe", timeout=10)
    return CheckResult("telegram", r.status_code == 200, f"HTTP {r.status_code}")


def check_gee(s, _client) -> CheckResult:
    if not s.gee_key_file:
        return CheckResult("gee", False, "GEE_KEY_FILE not set")
    try:
        import ee  # type: ignore

        credentials = ee.ServiceAccountCredentials(s.gee_service_account, s.gee_key_file)
        ee.Initialize(credentials, opt_url="https://earthengine-highvolume.googleapis.com")
        # cheap call
        _ = ee.Number(1).getInfo()
        return CheckResult("gee", True, "initialized")
    except Exception as e:
        return CheckResult("gee", False, f"{type(e).__name__}: {e}")


REQUIRED = {"openaq", "open_meteo", "firms"}


async def main() -> int:
    s = get_settings()
    async with httpx.AsyncClient() as client:
        checks: list[CheckResult] = await asyncio.gather(
            check_openaq(s, client),
            check_open_meteo(s, client),
            check_firms(s, client),
            check_groq(s, client),
            check_ollama(s, client),
            check_telegram(s, client),
        )
        checks.append(check_gee(s, client))

    width = max(len(c.name) for c in checks)
    failed_required = False
    for c in checks:
        status = "OK  " if c.ok else "FAIL"
        marker = " (required)" if c.name in REQUIRED else ""
        print(f"  {c.name.ljust(width)}  {status}  {c.detail}{marker}")
        if not c.ok and c.name in REQUIRED:
            failed_required = True

    if failed_required:
        print("\nOne or more required keys failed.", file=sys.stderr)
        return 1
    print("\nAll required keys OK.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
