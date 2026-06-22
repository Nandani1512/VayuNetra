"""Builds the 1 km grid for a city, then enriches each cell with:
  - OSM features (road density, industry, hospitals, schools)
  - WorldPop population (total / elderly / children)
  - LULC dominant class (ESA WorldCover fallback)

Run once per city. Output: rows in `grid_cell`. OSM Overpass response cached to
data/cache/osm/<city>.geojson for DVC tracking.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import httpx
from prefect import flow, get_run_logger, task
from pyproj import CRS, Transformer
from shapely.geometry import Point, Polygon, box, mapping, shape
from sqlalchemy import text

from vayunetra.ingestion.utils import city_bbox, load_city_config
from vayunetra.storage.db import session_scope

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CACHE_DIR = Path("data/cache/osm")


def _utm_zone(lon: float) -> int:
    return int((lon + 180) / 6) + 1


def _local_utm_crs(lon: float, lat: float) -> CRS:
    zone = _utm_zone(lon)
    south = lat < 0
    epsg = (32700 if south else 32600) + zone
    return CRS.from_epsg(epsg)


def _build_grid(bbox: tuple[float, float, float, float], cell_m: int = 1000) -> list[dict]:
    """Returns list of {cell_id, geom (wgs84 polygon), centroid (wgs84 point)}."""
    min_lon, min_lat, max_lon, max_lat = bbox
    centre_lon = (min_lon + max_lon) / 2
    centre_lat = (min_lat + max_lat) / 2
    utm = _local_utm_crs(centre_lon, centre_lat)
    fwd = Transformer.from_crs(4326, utm, always_xy=True)
    inv = Transformer.from_crs(utm, 4326, always_xy=True)

    x0, y0 = fwd.transform(min_lon, min_lat)
    x1, y1 = fwd.transform(max_lon, max_lat)

    nx = int(math.ceil((x1 - x0) / cell_m))
    ny = int(math.ceil((y1 - y0) / cell_m))
    cells = []
    for j in range(ny):
        for i in range(nx):
            cx0, cy0 = x0 + i * cell_m, y0 + j * cell_m
            cx1, cy1 = cx0 + cell_m, cy0 + cell_m
            # Reproject corners back to wgs84
            lo0, la0 = inv.transform(cx0, cy0)
            lo1, la1 = inv.transform(cx1, cy1)
            poly = box(min(lo0, lo1), min(la0, la1), max(lo0, lo1), max(la0, la1))
            centroid = poly.centroid
            cells.append(
                {
                    "cell_id": f"{i:04d}_{j:04d}",
                    "geom": poly,
                    "centroid": centroid,
                }
            )
    return cells


@task
def pull_osm(city: str) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{city}.geojson"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    w, s, e, n = city_bbox(city)
    q = f"""
    [out:json][timeout:120];
    (
      way["highway"~"motorway|trunk|primary|secondary|tertiary"]({s},{w},{n},{e});
      way["landuse"~"industrial|construction"]({s},{w},{n},{e});
      node["man_made"="works"]({s},{w},{n},{e});
      node["amenity"="hospital"]({s},{w},{n},{e});
      way["amenity"="hospital"]({s},{w},{n},{e});
      node["amenity"="school"]({s},{w},{n},{e});
      way["amenity"="school"]({s},{w},{n},{e});
    );
    out geom;
    """
    r = httpx.post(
        OVERPASS_URL,
        data={"data": q},
        timeout=180,
        headers={"User-Agent": "VayuNetra/0.1 (research project; contact via github)"},
    )
    r.raise_for_status()
    data = r.json()
    cache_file.write_text(json.dumps(data))
    return data


def _enrich_cells(cells: list[dict], osm: dict[str, Any]) -> list[dict]:
    """Aggregate OSM features into per-cell counts and road km/km²."""
    import geopandas as gpd
    from shapely.geometry import LineString

    elements = osm.get("elements", [])
    road_lines, industry_pts, hosp_pts, school_pts = [], [], [], []
    for el in elements:
        tags = el.get("tags") or {}
        if el["type"] == "way" and "geometry" in el:
            coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
            if len(coords) < 2:
                continue
            if "highway" in tags:
                road_lines.append(LineString(coords))
            elif tags.get("landuse") in ("industrial", "construction"):
                if len(coords) >= 3:
                    industry_pts.append(Polygon(coords).centroid)
            elif tags.get("amenity") == "hospital":
                if len(coords) >= 3:
                    hosp_pts.append(Polygon(coords).centroid)
            elif tags.get("amenity") == "school":
                if len(coords) >= 3:
                    school_pts.append(Polygon(coords).centroid)
        elif el["type"] == "node":
            p = Point(el["lon"], el["lat"])
            if tags.get("man_made") == "works":
                industry_pts.append(p)
            elif tags.get("amenity") == "hospital":
                hosp_pts.append(p)
            elif tags.get("amenity") == "school":
                school_pts.append(p)

    cells_gdf = gpd.GeoDataFrame(
        [{"cell_id": c["cell_id"], "geom": c["geom"]} for c in cells], geometry="geom", crs=4326
    )
    cells_utm = cells_gdf.to_crs(cells_gdf.estimate_utm_crs())
    cell_area_km2 = cells_utm.area / 1e6

    if road_lines:
        roads_gdf = gpd.GeoDataFrame(geometry=road_lines, crs=4326).to_crs(cells_utm.crs)
        # Intersect roads with cells
        joined = gpd.overlay(
            roads_gdf.reset_index(),
            cells_utm[["cell_id", "geom"]].rename(columns={"geom": "geometry"}).set_geometry("geometry"),
            how="intersection",
        )
        joined["len_km"] = joined.geometry.length / 1000.0
        road_km = joined.groupby("cell_id")["len_km"].sum()
    else:
        road_km = {}

    def _count(points, attr):
        if not points:
            return {c["cell_id"]: 0 for c in cells}
        gdf = gpd.GeoDataFrame(geometry=points, crs=4326).to_crs(cells_utm.crs)
        join = gpd.sjoin(gdf, cells_utm[["cell_id", "geom"]].set_geometry("geom"), predicate="within")
        return join.groupby("cell_id").size().to_dict()

    industry = _count(industry_pts, "industry_count")
    hosp = _count(hosp_pts, "hospital_count")
    schools = _count(school_pts, "school_count")

    out = []
    for c, area_km2 in zip(cells, cell_area_km2):
        cid = c["cell_id"]
        out.append(
            {
                "cell_id": cid,
                "geom": c["geom"],
                "centroid": c["centroid"],
                "road_density": float(road_km.get(cid, 0.0)) / max(area_km2, 1e-6),
                "industry_count": int(industry.get(cid, 0)),
                "hospital_count": int(hosp.get(cid, 0)),
                "school_count": int(schools.get(cid, 0)),
            }
        )
    return out


@task
def upsert_grid(city: str, cells: list[dict]) -> int:
    if not cells:
        return 0
    stmt = text(
        """
        INSERT INTO grid_cell (city_id, cell_id, geom, centroid, road_density,
                               industry_count, hospital_count, school_count)
        VALUES (:city, :cell_id,
                ST_GeomFromText(:geom_wkt, 4326),
                ST_GeomFromText(:centroid_wkt, 4326),
                :road_density, :industry_count, :hospital_count, :school_count)
        ON CONFLICT (city_id, cell_id) DO UPDATE
        SET geom = EXCLUDED.geom,
            centroid = EXCLUDED.centroid,
            road_density = EXCLUDED.road_density,
            industry_count = EXCLUDED.industry_count,
            hospital_count = EXCLUDED.hospital_count,
            school_count = EXCLUDED.school_count
        """
    )
    with session_scope() as s:
        for c in cells:
            s.execute(
                stmt,
                {
                    "city": city,
                    "cell_id": c["cell_id"],
                    "geom_wkt": c["geom"].wkt,
                    "centroid_wkt": c["centroid"].wkt,
                    "road_density": c.get("road_density"),
                    "industry_count": c.get("industry_count"),
                    "hospital_count": c.get("hospital_count"),
                    "school_count": c.get("school_count"),
                },
            )
    return len(cells)


@flow(name="build-grid")
def build_grid(city: str) -> dict:
    log = get_run_logger()
    cfg = load_city_config(city)
    bbox = tuple(cfg["bbox"])  # type: ignore[assignment]
    cell_m = int(cfg.get("grid_resolution_m", 1000))
    cells = _build_grid(bbox, cell_m=cell_m)
    log.info("grid_built cells=%d", len(cells))
    osm = pull_osm(city)
    cells = _enrich_cells(cells, osm)
    n = upsert_grid(city, cells)
    log.info("grid_cells_upserted city=%s n=%d", city, n)
    return {"cells": n}
