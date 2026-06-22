"""Shared pytest fixtures."""

from __future__ import annotations

import os
import pytest


@pytest.fixture(autouse=True)
def _env_defaults(monkeypatch):
    # Make sure tests never accidentally hit production resources.
    monkeypatch.setenv("ENABLE_LANGGRAPH", "false")
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv(
        "DATABASE_URL",
        os.environ.get(
            "DATABASE_URL",
            "postgresql+psycopg://vayunetra:vayunetra@localhost:5432/vayunetra",
        ),
    )
