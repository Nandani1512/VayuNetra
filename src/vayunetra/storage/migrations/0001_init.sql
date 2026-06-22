-- VayuNetra base schema. Idempotent.
-- Extensions are created by deploy/postgres/init.sql at container init.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;

-- ── Cities ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS city (
  id           text PRIMARY KEY,
  name         text NOT NULL,
  bbox         geometry(Polygon, 4326) NOT NULL,
  default_lang text NOT NULL,
  timezone     text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ── Stations ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS station (
  id          text PRIMARY KEY,
  source      text NOT NULL,
  city_id     text REFERENCES city(id) ON DELETE CASCADE,
  name        text,
  geom        geometry(Point, 4326) NOT NULL,
  elevation_m real,
  attrs       jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS station_geom_idx ON station USING gist(geom);
CREATE INDEX IF NOT EXISTS station_city_idx ON station(city_id);

-- ── Observations (hypertable) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS observation (
  station_id  text NOT NULL REFERENCES station(id) ON DELETE CASCADE,
  ts          timestamptz NOT NULL,
  pollutant   text NOT NULL,
  value       real NOT NULL,
  unit        text NOT NULL,
  qa          real,
  source      text NOT NULL,
  PRIMARY KEY (station_id, ts, pollutant)
);
SELECT create_hypertable('observation','ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS observation_pollutant_ts_idx ON observation (pollutant, ts DESC);

-- ── Weather (hypertable) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather (
  station_id text NOT NULL REFERENCES station(id) ON DELETE CASCADE,
  ts         timestamptz NOT NULL,
  temp_c     real,
  wind_u     real,
  wind_v     real,
  pbl_m      real,
  rh_pct     real,
  precip_mm  real,
  PRIMARY KEY (station_id, ts)
);
SELECT create_hypertable('weather','ts', if_not_exists => TRUE);

-- ── Satellite columns (hypertable) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS satellite_column (
  ts        timestamptz NOT NULL,
  product   text NOT NULL,
  cell      geometry(Polygon, 4326) NOT NULL,
  city_id   text NOT NULL REFERENCES city(id) ON DELETE CASCADE,
  value     real NOT NULL,
  qa        real,
  cell_key  text NOT NULL,
  PRIMARY KEY (ts, product, cell_key)
);
SELECT create_hypertable('satellite_column','ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS satellite_geom_idx ON satellite_column USING gist(cell);
CREATE INDEX IF NOT EXISTS satellite_city_product_idx ON satellite_column(city_id, product, ts DESC);

-- ── Fire events ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fire_event (
  id         bigserial PRIMARY KEY,
  ts         timestamptz NOT NULL,
  geom       geometry(Point, 4326) NOT NULL,
  brightness real,
  frp        real,
  confidence text,
  sensor     text
);
CREATE INDEX IF NOT EXISTS fire_geom_idx ON fire_event USING gist(geom);
CREATE INDEX IF NOT EXISTS fire_ts_idx ON fire_event (ts DESC);

-- ── 1 km grid cells ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS grid_cell (
  city_id        text NOT NULL REFERENCES city(id) ON DELETE CASCADE,
  cell_id        text NOT NULL,
  geom           geometry(Polygon, 4326) NOT NULL,
  centroid       geometry(Point, 4326) NOT NULL,
  pop_total      real,
  pop_elderly    real,
  pop_children   real,
  road_density   real,
  industry_count integer,
  hospital_count integer,
  school_count   integer,
  lulc_class     text,
  elevation_m    real,
  PRIMARY KEY (city_id, cell_id)
);
CREATE INDEX IF NOT EXISTS grid_geom_idx ON grid_cell USING gist(geom);
CREATE INDEX IF NOT EXISTS grid_centroid_idx ON grid_cell USING gist(centroid);

-- ── Forecasts ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS forecast (
  city_id       text NOT NULL,
  cell_id       text NOT NULL,
  ts_issued     timestamptz NOT NULL,
  ts_target     timestamptz NOT NULL,
  pollutant     text NOT NULL,
  p10           real,
  p50           real,
  p90           real,
  model_version text NOT NULL,
  PRIMARY KEY (city_id, cell_id, ts_issued, ts_target, pollutant)
);
SELECT create_hypertable('forecast','ts_target', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS forecast_lookup_idx
  ON forecast (city_id, pollutant, ts_target DESC, ts_issued DESC);

-- ── Enforcement audit log ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS enforcement_log (
  id            bigserial PRIMARY KEY,
  ts            timestamptz NOT NULL DEFAULT now(),
  user_id       text NOT NULL,
  city_id       text NOT NULL,
  hotspot_geom  geometry(Polygon, 4326),
  inputs_json   jsonb NOT NULL,
  brief_text    text NOT NULL,
  model_version text NOT NULL
);
CREATE INDEX IF NOT EXISTS enforcement_ts_idx ON enforcement_log(ts DESC);

-- ── RAG knowledge base (pgvector) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS rag_chunk (
  id          bigserial PRIMARY KEY,
  source      text NOT NULL,
  title       text NOT NULL,
  content     text NOT NULL,
  embedding   vector(384),
  meta        jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rag_embedding_idx
  ON rag_chunk USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ── Seed cities ──────────────────────────────────────────────────────────
INSERT INTO city (id, name, bbox, default_lang, timezone)
VALUES
  ('delhi', 'Delhi',
     ST_MakeEnvelope(76.84, 28.40, 77.35, 28.88, 4326)::geometry(Polygon, 4326),
     'hi', 'Asia/Kolkata'),
  ('bengaluru', 'Bengaluru',
     ST_MakeEnvelope(77.45, 12.83, 77.78, 13.14, 4326)::geometry(Polygon, 4326),
     'kn', 'Asia/Kolkata')
ON CONFLICT (id) DO NOTHING;
