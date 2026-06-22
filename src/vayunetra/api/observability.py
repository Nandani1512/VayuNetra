"""Observability wiring for the FastAPI app (Phase 10).

Three concerns, each independently fail-safe so a missing collector or library
never takes the API down:

  1. **Prometheus** — request counter, request-duration histogram and a
     ``vayunetra_inference_latency_seconds`` histogram, all recorded by our own
     middleware and exposed at ``/metrics`` via ``prometheus_client``. We record
     against the *matched route template* (e.g. ``/forecast/cell``) rather than
     the raw URL to keep label cardinality bounded. We deliberately avoid
     ``prometheus-fastapi-instrumentator``'s middleware here because its route
     resolver is incompatible with mounted sub-apps in this Starlette version.
  2. **OpenTelemetry** — FastAPI auto-instrumentation with an OTLP exporter to
     the collector at ``settings.otel_exporter_otlp_endpoint``. Spans carry the
     service name ``settings.otel_service_name``.
  3. **Structured request logging** — a middleware that stamps every request
     with a ``request_id`` (and the active OTel ``trace_id`` when present),
     binds them into structlog contextvars, and emits one access log line with
     method, path, status and duration.

Call :func:`setup_observability(app)` once during app construction.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import structlog
from starlette.responses import Response

from vayunetra.common.config import get_settings
from vayunetra.common.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

log = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Prometheus metrics (module-level singletons on the default registry).
# --------------------------------------------------------------------------- #
try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        REGISTRY,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    HTTP_REQUESTS = Counter(
        "vayunetra_http_requests_total",
        "Total HTTP requests",
        ["method", "handler", "status"],
    )
    HTTP_LATENCY = Histogram(
        "vayunetra_http_request_duration_seconds",
        "HTTP request latency in seconds",
        ["method", "handler"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 8.0),
    )
    # SLO histogram the Grafana/Prometheus alerts key off (plan §10).
    INFERENCE_LATENCY = Histogram(
        "vayunetra_inference_latency_seconds",
        "Per-endpoint served latency (SLO target)",
        ["handler"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.5, 4.0, 8.0),
    )
    # Model-performance gauges, populated from the latest eval report so the
    # Grafana "Model performance" dashboard reflects the most recent
    # `make eval` run (API and eval share the repo filesystem on the demo box).
    FORECAST_RMSE = Gauge(
        "vayunetra_forecast_rmse",
        "Latest walk-forward RMSE of the forecast model",
        ["city", "pollutant", "horizon"],
    )
    FORECAST_LIFT = Gauge(
        "vayunetra_forecast_lift_vs_persistence",
        "Latest forecast RMSE lift vs persistence (fraction)",
        ["city", "pollutant", "horizon"],
    )
    _PROM_OK = True
except Exception:  # pragma: no cover - prometheus_client always installed
    _PROM_OK = False


def _route_template(request) -> str:  # type: ignore[no-untyped-def]
    """Matched route path (e.g. ``/forecast/cell``) or a bounded fallback."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return str(path)
    raw = request.url.path
    # Bound cardinality for unmatched paths (404s, static assets).
    return "/static/*" if raw.startswith("/static") else "__other__"


def _setup_otel(app: "FastAPI") -> bool:
    settings = get_settings()
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": settings.otel_service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        return True
    except Exception as e:  # pragma: no cover - defensive
        log.warning("otel_setup_failed", error=str(e))
        return False


def _current_trace_id() -> str | None:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx and ctx.trace_id:
            return format(ctx.trace_id, "032x")
    except Exception:
        return None
    return None


def _expose_metrics(app: "FastAPI") -> None:
    if not _PROM_OK:
        return

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:  # type: ignore[no-untyped-def]
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


def _install_request_middleware(app: "FastAPI") -> None:
    from starlette.requests import Request

    @app.middleware("http")
    async def _observe(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        bind = {"request_id": request_id}
        trace_id = _current_trace_id()
        if trace_id:
            bind["trace_id"] = trace_id
        structlog.contextvars.bind_contextvars(**bind)

        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = request_id
            return response
        finally:
            duration = time.perf_counter() - start
            handler = _route_template(request)
            if _PROM_OK and handler != "/metrics":
                try:
                    HTTP_REQUESTS.labels(request.method, handler, str(status)).inc()
                    HTTP_LATENCY.labels(request.method, handler).observe(duration)
                    INFERENCE_LATENCY.labels(handler).observe(duration)
                except Exception:  # pragma: no cover
                    pass
            log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                handler=handler,
                status=status,
                duration_ms=round(duration * 1000.0, 2),
                client=request.client.host if request.client else None,
            )
            structlog.contextvars.clear_contextvars()


def publish_latest_eval_metrics(city: str = "delhi", pollutant: str = "pm25") -> bool:
    """Load the newest ``reports/eval_*/results.json`` and publish forecast
    RMSE/lift to Prometheus gauges so the Model-Performance dashboard reflects
    the last ``make eval`` run. Best-effort; never raises.
    """
    if not _PROM_OK:
        return False
    try:
        import json as _json
        from pathlib import Path as _Path

        reports = _Path(__file__).resolve().parents[3] / "reports"
        runs = sorted(reports.glob("eval_*/results.json"))
        if not runs:
            return False
        data = _json.loads(runs[-1].read_text())
        forecast = next((r for r in data if r.get("name") == "forecast"), None)
        if not forecast:
            return False
        for row in forecast.get("metrics", {}).get("per_horizon", []):
            h = str(int(row["horizon_h"]))
            if row.get("rmse_model") is not None and not _isnan(row["rmse_model"]):
                FORECAST_RMSE.labels(city, pollutant, h).set(float(row["rmse_model"]))
            if row.get("lift_vs_persistence") is not None and not _isnan(
                row["lift_vs_persistence"]
            ):
                FORECAST_LIFT.labels(city, pollutant, h).set(float(row["lift_vs_persistence"]))
        log.info("eval_metrics_published", run=str(runs[-1].parent.name))
        return True
    except Exception as e:  # pragma: no cover
        log.warning("eval_metrics_publish_failed", error=str(e))
        return False


def _isnan(x: object) -> bool:
    try:
        return x != x  # NaN is the only value not equal to itself
    except Exception:
        return False


def setup_observability(app: "FastAPI") -> dict[str, bool]:
    """Wire Prometheus, OTel and structured request logging onto ``app``.

    Returns a small status map (useful for ``/healthz`` introspection / tests).
    Each component is best-effort and never raises.
    """
    status = {"prometheus": _PROM_OK, "otel": False, "request_logging": False}
    try:
        _expose_metrics(app)
    except Exception as e:  # pragma: no cover
        log.warning("metrics_endpoint_failed", error=str(e))
        status["prometheus"] = False
    try:
        _install_request_middleware(app)
        status["request_logging"] = True
    except Exception as e:  # pragma: no cover
        log.warning("request_logging_setup_failed", error=str(e))
    status["otel"] = _setup_otel(app)
    publish_latest_eval_metrics()
    log.info("observability_ready", **status)
    return status
