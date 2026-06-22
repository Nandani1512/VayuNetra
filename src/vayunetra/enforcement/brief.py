"""Generate court/audit-ready enforcement briefs via an LLM.

Backend selection:
  1. If GROQ_API_KEY is set → Groq llama-3.3-70b (cloud, fast).
  2. Else fall back to Ollama llama3.1:8b at OLLAMA_BASE_URL.
  3. Else (no LLM available) return a deterministic template-only brief.

Every call writes a row to enforcement_log with full inputs + brief text +
model version (rubric §10 auditability).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import text

from vayunetra.common.config import get_settings
from vayunetra.common.logging import get_logger
from vayunetra.storage.db import session_scope

log = get_logger(__name__)


SYSTEM = """You are an air-quality enforcement assistant. Given a polluted hotspot,
generate a concise, court-ready brief. Cite every numeric claim in square
brackets, e.g. [forecast p50=180µg/m³] or [SHAP: vehicular=42%]. Output sections:

1. Executive Summary (2 sentences)
2. Forecast & Severity
3. Source Attribution
4. Registered Emitters within 500 m
5. Recommended Actions (bulleted, prioritised)
6. Citations

Be specific and tactical. Do not hallucinate values; use only what is provided."""


def _user_payload(ctx: dict[str, Any]) -> str:
    return (
        "CONTEXT (JSON):\n```\n"
        + json.dumps(ctx, indent=2, default=str)
        + "\n```\n\nDraft the enforcement brief now."
    )


async def _call_groq(prompt: str, api_key: str, model: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 800,
            },
        )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


async def _call_ollama(prompt: str, base_url: str, model: str) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.2},
            },
        )
    r.raise_for_status()
    return r.json()["message"]["content"]


def _template_brief(ctx: dict[str, Any]) -> str:
    c = ctx["cluster"]
    a = ctx.get("attribution", {})
    return (
        f"## Executive Summary\n"
        f"Cluster #{c.get('rank','?')} in {ctx['city']} shows mean PM2.5 = "
        f"{c['mean_p50']:.0f} µg/m³ over {c['n_cells']} cells, "
        f"with {int(c['pop_exposed']):,} residents exposed.\n\n"
        f"## Forecast & Severity\n"
        f"[forecast horizon={ctx.get('horizon_h')}h, p50={c['mean_p50']:.0f}, max={c['max_p50']:.0f}]\n\n"
        f"## Source Attribution\n"
        + (
            "".join(f"- {k}: {v*100:.1f}%\n" for k, v in (a.get('blended_sources') or {}).items())
            or "- (no SHAP run — train LUR first)\n"
        )
        + f"\n## Registered Emitters within 500 m\n"
        f"- industry POIs: {c.get('emitters',{}).get('industry',0)}\n"
        f"- schools: {c.get('emitters',{}).get('schools',0)}\n"
        f"- hospitals: {c.get('emitters',{}).get('hospitals',0)}\n"
        f"- road density: {c.get('emitters',{}).get('road_density',0):.2f} km/km²\n\n"
        f"## Recommended Actions\n"
        f"- Dispatch inspector to centroid ({c['centroid_lat']:.4f}, {c['centroid_lon']:.4f}).\n"
        f"- Cross-check construction permits and operating-hour compliance.\n"
        f"- Issue advisory to schools/hospitals within the cluster.\n\n"
        f"## Citations\n"
        f"- Forecast model run: {ctx.get('model_version','unknown')}\n"
        f"- Generated at: {datetime.now(timezone.utc).isoformat()}\n"
    )


def _log_enforcement(user_id: str, city: str, ctx: dict[str, Any], brief: str, model_version: str) -> None:
    sql = text(
        """
        INSERT INTO enforcement_log (user_id, city_id, inputs_json, brief_text, model_version)
        VALUES (:user_id, :city, CAST(:inputs AS jsonb), :brief, :model_version)
        """
    )
    with session_scope() as s:
        s.execute(
            sql,
            {
                "user_id": user_id,
                "city": city,
                "inputs": json.dumps(ctx, default=str),
                "brief": brief,
                "model_version": model_version,
            },
        )


async def generate_brief(
    user_id: str,
    city: str,
    cluster: dict[str, Any],
    attribution: dict[str, Any] | None = None,
    horizon_h: int = 24,
    pollutant: str = "pm25",
) -> dict[str, Any]:
    settings = get_settings()
    ctx = {
        "city": city,
        "horizon_h": horizon_h,
        "pollutant": pollutant,
        "cluster": cluster,
        "attribution": attribution or {},
        "model_version": (attribution or {}).get("model_version", "unknown"),
    }
    prompt = _user_payload(ctx)

    model_used = "template"
    brief: str
    try:
        if settings.groq_api_key:
            brief = await _call_groq(prompt, settings.groq_api_key, settings.groq_model)
            model_used = f"groq/{settings.groq_model}"
        else:
            brief = await _call_ollama(prompt, settings.ollama_base_url, settings.ollama_model)
            model_used = f"ollama/{settings.ollama_model}"
    except Exception as e:
        log.warning("llm_failed_using_template", error=str(e))
        brief = _template_brief(ctx)

    try:
        _log_enforcement(user_id, city, ctx, brief, model_used)
    except Exception as e:
        log.warning("audit_log_failed", error=str(e))

    return {"brief": brief, "model": model_used, "ctx": ctx}
