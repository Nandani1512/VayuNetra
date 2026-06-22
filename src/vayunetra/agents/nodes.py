"""LangGraph node functions."""

from __future__ import annotations

import json
import logging
from typing import Any

from vayunetra.agents.state import AgentState
from vayunetra.common.config import get_settings

logger = logging.getLogger(__name__)


def _get_llm():
    from langchain_groq import ChatGroq

    s = get_settings()
    return ChatGroq(api_key=s.groq_api_key, model=s.groq_model, temperature=0)


def router_node(state: AgentState) -> dict[str, Any]:
    """Classify user intent via LLM."""
    llm = _get_llm()
    msg = state["user_message"]
    resp = llm.invoke(
        f"Classify the following user message into exactly one category: "
        f"forecast, attribution, enforce, advisory, general.\n"
        f"Reply with ONLY the category word.\n\nMessage: {msg}"
    )
    intent = resp.content.strip().lower()
    if intent not in ("forecast", "attribution", "enforce", "advisory", "general"):
        intent = "general"
    return {"intent": intent}


def forecast_node(state: AgentState) -> dict[str, Any]:
    """Run forecast tool."""
    try:
        from vayunetra.models.forecast.lightgbm_trainer import predict

        result = predict(city="delhi", pollutant="pm25", horizon=24)
    except Exception as e:
        result = {"error": str(e)}
    return {"tool_result": result}


def attribution_node(state: AgentState) -> dict[str, Any]:
    """Run attribution tool."""
    try:
        from vayunetra.models.attribution.overlay import attribute_cell

        result = attribute_cell(lat=28.6, lon=77.2)
    except Exception as e:
        result = {"error": str(e)}
    return {"tool_result": result}


def enforce_node(state: AgentState) -> dict[str, Any]:
    """Run enforcement tool."""
    try:
        from vayunetra.enforcement.service import enforce

        result = enforce(city="delhi", top_k=5)
    except Exception as e:
        result = {"error": str(e)}
    return {"tool_result": result}


def advisory_node(state: AgentState) -> dict[str, Any]:
    """Run advisory tool."""
    try:
        from vayunetra.advisory.templates import render, severity_for

        sev = severity_for(aqi=150)
        result = render(severity=sev, language="en", tier="general")
    except Exception as e:
        result = {"error": str(e)}
    return {"tool_result": result}


def general_node(state: AgentState) -> dict[str, Any]:
    """Handle general queries."""
    return {"tool_result": "I'm VayuNetra, an air quality intelligence assistant. I can help with forecasts, pollution attribution, enforcement hotspots, and health advisories."}


def composer_node(state: AgentState) -> dict[str, Any]:
    """Compose natural language response from tool result."""
    llm = _get_llm()
    tool_result = state.get("tool_result", "No data available.")
    user_msg = state["user_message"]
    resp = llm.invoke(
        f"You are VayuNetra, an air quality assistant. "
        f"The user asked: {user_msg}\n\n"
        f"Tool returned: {json.dumps(tool_result, default=str)}\n\n"
        f"Provide a concise, helpful natural language response."
    )
    return {"response": resp.content}
