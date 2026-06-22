"""SQLAlchemy + GeoAlchemy2 ORM models mirroring migrations/0001_init.sql."""

from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class City(Base):
    __tablename__ = "city"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    bbox = mapped_column(Geometry("POLYGON", srid=4326), nullable=False)
    default_lang: Mapped[str] = mapped_column(String, nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    stations: Mapped[list["Station"]] = relationship(back_populates="city")


class Station(Base):
    __tablename__ = "station"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    city_id: Mapped[str | None] = mapped_column(ForeignKey("city.id", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(String)
    geom = mapped_column(Geometry("POINT", srid=4326), nullable=False)
    elevation_m: Mapped[float | None] = mapped_column(Float)
    attrs: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    city: Mapped[City | None] = relationship(back_populates="stations")


class Observation(Base):
    __tablename__ = "observation"

    station_id: Mapped[str] = mapped_column(
        ForeignKey("station.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    pollutant: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    qa: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String, nullable=False)


class Weather(Base):
    __tablename__ = "weather"

    station_id: Mapped[str] = mapped_column(
        ForeignKey("station.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    temp_c: Mapped[float | None] = mapped_column(Float)
    wind_u: Mapped[float | None] = mapped_column(Float)
    wind_v: Mapped[float | None] = mapped_column(Float)
    pbl_m: Mapped[float | None] = mapped_column(Float)
    rh_pct: Mapped[float | None] = mapped_column(Float)
    precip_mm: Mapped[float | None] = mapped_column(Float)


class SatelliteColumn(Base):
    __tablename__ = "satellite_column"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    product: Mapped[str] = mapped_column(String, primary_key=True)
    cell_key: Mapped[str] = mapped_column(String, primary_key=True)
    cell = mapped_column(Geometry("POLYGON", srid=4326), nullable=False)
    city_id: Mapped[str] = mapped_column(ForeignKey("city.id", ondelete="CASCADE"))
    value: Mapped[float] = mapped_column(Float, nullable=False)
    qa: Mapped[float | None] = mapped_column(Float)


class FireEvent(Base):
    __tablename__ = "fire_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    geom = mapped_column(Geometry("POINT", srid=4326), nullable=False)
    brightness: Mapped[float | None] = mapped_column(Float)
    frp: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[str | None] = mapped_column(String)
    sensor: Mapped[str | None] = mapped_column(String)


class GridCell(Base):
    __tablename__ = "grid_cell"

    city_id: Mapped[str] = mapped_column(
        ForeignKey("city.id", ondelete="CASCADE"), primary_key=True
    )
    cell_id: Mapped[str] = mapped_column(String, primary_key=True)
    geom = mapped_column(Geometry("POLYGON", srid=4326), nullable=False)
    centroid = mapped_column(Geometry("POINT", srid=4326), nullable=False)
    pop_total: Mapped[float | None] = mapped_column(Float)
    pop_elderly: Mapped[float | None] = mapped_column(Float)
    pop_children: Mapped[float | None] = mapped_column(Float)
    road_density: Mapped[float | None] = mapped_column(Float)
    industry_count: Mapped[int | None] = mapped_column(Integer)
    hospital_count: Mapped[int | None] = mapped_column(Integer)
    school_count: Mapped[int | None] = mapped_column(Integer)
    lulc_class: Mapped[str | None] = mapped_column(String)
    elevation_m: Mapped[float | None] = mapped_column(Float)


class Forecast(Base):
    __tablename__ = "forecast"

    city_id: Mapped[str] = mapped_column(String, primary_key=True)
    cell_id: Mapped[str] = mapped_column(String, primary_key=True)
    ts_issued: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    ts_target: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    pollutant: Mapped[str] = mapped_column(String, primary_key=True)
    p10: Mapped[float | None] = mapped_column(Float)
    p50: Mapped[float | None] = mapped_column(Float)
    p90: Mapped[float | None] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String, nullable=False)


class EnforcementLog(Base):
    __tablename__ = "enforcement_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    city_id: Mapped[str] = mapped_column(String, nullable=False)
    hotspot_geom = mapped_column(Geometry("POLYGON", srid=4326), nullable=True)
    inputs_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    brief_text: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(String, nullable=False)
