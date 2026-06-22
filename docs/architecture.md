# VayuNetra — System Architecture

```mermaid
graph TB
    %% Data Sources
    subgraph Sources["Data Sources"]
        OAQ[OpenAQ<br/>60 stations Delhi]
        OME[Open-Meteo<br/>Weather grids]
        S5P[Sentinel-5P<br/>NO₂/SO₂ columns]
        MOD[MODIS<br/>AOD retrievals]
        FIR[FIRMS<br/>Active fires]
        CAM[CAMS<br/>Reanalysis]
        OSM[OpenStreetMap<br/>Land use/roads]
        WPO[WorldPop<br/>Population density]
    end

    %% Ingestion
    subgraph Ingestion["Ingestion Layer — Prefect"]
        PF1[Station poller]
        PF2[Satellite fetcher]
        PF3[Weather sync]
        PF4[Static features]
    end

    %% Storage
    subgraph Storage["Storage"]
        PG[(PostGIS +<br/>TimescaleDB)]
        S3[MinIO<br/>Object store]
        RD[Redis<br/>Cache + queues]
    end

    %% ML Pipeline
    subgraph ML["ML Pipeline"]
        FC[LightGBM<br/>Forecast 1-72h]
        LUR[LUR Downscaler<br/>1km grid]
        SH[SHAP<br/>Attribution]
    end

    %% Enforcement
    subgraph Enforce["Enforcement Intelligence"]
        GI[Gi* Hotspots]
        DB[DBSCAN Clusters]
        LLM[LLM Briefs]
    end

    %% Serving
    subgraph Serve["Serving"]
        API[FastAPI<br/>REST + WebSocket]
        LG[LangGraph<br/>Supervisor Agent]
    end

    %% Channels
    subgraph Chan["Channels"]
        UI[Web UI<br/>MapLibre]
        TG[Telegram Bot]
        IVR[IVR Gateway]
    end

    %% Evaluation
    subgraph Eval["Evaluation Harness"]
        EP[Persistence gate<br/>RMSE / lift]
        EA[Attribution accuracy<br/>≤25pp vs SAFAR]
        EC[chrF + RAG<br/>citation rate]
        EL[Latency p50/p95]
    end

    %% Edges
    Sources --> Ingestion
    Ingestion --> PG
    Ingestion --> S3
    PG --> ML
    S3 --> ML
    RD --> Serve
    ML --> PG
    ML --> Enforce
    Enforce --> PG
    PG --> Serve
    Serve --> Chan
    ML --> Eval
    Serve --> Eval
```

## Component Summary

| Layer | Technology | Role |
|-------|-----------|------|
| Ingestion | Prefect 2 | Orchestrated ETL flows with retry + backfill |
| Storage | PostGIS + TimescaleDB | Spatiotemporal hypertables, 1km grid (3 955 cells) |
| Object store | MinIO | Satellite rasters, model artifacts (DVC-tracked) |
| Cache | Redis | Tile cache, rate-limit counters, pub/sub |
| Forecast | LightGBM | 1–72 h ahead, RMSE 47.2 µg/m³ |
| Downscaling | Land-Use Regression | Fuses static features → 1 km resolution |
| Attribution | SHAP | Source-category % contribution |
| Hotspots | Getis-Ord Gi* + DBSCAN | Spatial clustering for enforcement |
| Serving | FastAPI | REST endpoints, Prometheus /metrics |
| Agent | LangGraph | Multi-tool supervisor (forecast, advisory, enforce) |
| UI | MapLibre GL JS | Interactive choropleth + timeline |
| Bot | Telegram + IVR | 12 Indian languages, RAG-backed advisories |
| Eval | Custom harness | Persistence lift, SAFAR benchmark, chrF |
