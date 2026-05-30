"""SQLAlchemy 2.0 models for the Pollino hazelnut telemetry store.

Schema conventions (timeseries-db skill):
  stations(dim) ← sensors(dim) ← sensor_readings(fact, PK (sensor_id, ts))
  daily_water_balance(PK station_id, date) — wide per-day computed balance
  forecast_risk(PK station_id, issued_at, target_date)

Rules:
  - All timestamps: DateTime(timezone=True) → PostgreSQL TIMESTAMPTZ.
  - All measurements: NUMERIC(precision, scale) — never FLOAT.
  - CHECK constraints encode physical plausibility (et0 0–15 mm, ks 0–1).
  - BRIN indexes on time columns (append-only).
  - Composite (sensor_id, ts DESC) B-tree for latest-reading queries.
  - All writes idempotent via INSERT … ON CONFLICT DO UPDATE.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# Shared PostgreSQL ENUM type — created once, referenced by two tables.
_risk_level = Enum("G", "Y", "R", name="risk_level")


class Station(Base):
    """Dimension: a physical monitoring station (Piana di Cammarata)."""

    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(200))
    latitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(9, 6))
    elevation_m: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 1))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sensors: Mapped[list[Sensor]] = relationship(back_populates="station")
    balances: Mapped[list[DailyWaterBalance]] = relationship(back_populates="station")
    forecasts: Mapped[list[ForecastRisk]] = relationship(back_populates="station")


class Sensor(Base):
    """Dimension: a physical sensor on a station.

    sensor_type values: air_temp, solar_rad, wind_speed, soil_moisture.
    depth_cm is NULL for atmospheric sensors; 30 or 60 for soil probes.
    """

    __tablename__ = "sensors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sensor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    depth_cm: Mapped[Optional[int]] = mapped_column(SmallInteger)
    unit: Mapped[Optional[str]] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    station: Mapped[Station] = relationship(back_populates="sensors")
    readings: Mapped[list[SensorReading]] = relationship(back_populates="sensor")


class SensorReading(Base):
    """Fact: one raw measurement per sensor per timestep.

    Composite PK (sensor_id, ts) enforces idempotent upserts.
    BRIN on ts — optimal for append-only time-series range scans.
    Composite (sensor_id, ts DESC) B-tree — fast "latest reading" lookup.
    """

    __tablename__ = "sensor_readings"
    __table_args__ = (
        Index("ix_sensor_readings_ts_brin", "ts", postgresql_using="brin"),
        Index(
            "ix_sensor_readings_sensor_ts_desc",
            "sensor_id",
            sa.text("ts DESC"),
        ),
    )

    sensor_id: Mapped[int] = mapped_column(
        ForeignKey("sensors.id", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)

    sensor: Mapped[Sensor] = relationship(back_populates="readings")


class DailyWaterBalance(Base):
    """Fact (wide): computed daily soil-water balance per station.

    One row per (station_id, date); idempotent via ON CONFLICT DO UPDATE.
    Columns mirror the agronomy core outputs from et0, water_balance, stress, risk.
    CHECK constraints encode the same physical domain rules as the pure core.
    """

    __tablename__ = "daily_water_balance"
    __table_args__ = (
        CheckConstraint(
            "et0_mm IS NULL OR (et0_mm >= 0 AND et0_mm <= 15)",
            name="ck_dwb_et0_range",
        ),
        CheckConstraint(
            "ks IS NULL OR (ks >= 0 AND ks <= 1)",
            name="ck_dwb_ks_range",
        ),
        CheckConstraint(
            "etc_mm IS NULL OR etc_mm >= 0",
            name="ck_dwb_etc_nonneg",
        ),
        CheckConstraint(
            "precip_mm IS NULL OR precip_mm >= 0",
            name="ck_dwb_precip_nonneg",
        ),
        CheckConstraint(
            "irrigation_m3 IS NULL OR irrigation_m3 >= 0",
            name="ck_dwb_irrig_m3_nonneg",
        ),
        CheckConstraint(
            "irrigation_mm IS NULL OR irrigation_mm >= 0",
            name="ck_dwb_irrig_mm_nonneg",
        ),
        CheckConstraint(
            "taw_mm IS NULL OR taw_mm > 0",
            name="ck_dwb_taw_pos",
        ),
        CheckConstraint(
            "raw_mm IS NULL OR raw_mm >= 0",
            name="ck_dwb_raw_nonneg",
        ),
        CheckConstraint(
            "dr_mm IS NULL OR dr_mm >= 0",
            name="ck_dwb_dr_nonneg",
        ),
        CheckConstraint(
            "vpd_kpa IS NULL OR vpd_kpa >= 0",
            name="ck_dwb_vpd_nonneg",
        ),
        Index("ix_dwb_date_brin", "date", postgresql_using="brin"),
    )

    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
    )
    date: Mapped[_date] = mapped_column(Date, primary_key=True, nullable=False)
    et0_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    kc: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))
    etc_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    ks: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))
    precip_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    irrigation_m3: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2))
    irrigation_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    taw_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    raw_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    dr_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    vpd_kpa: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 3))
    risk_level: Mapped[Optional[str]] = mapped_column(_risk_level)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    station: Mapped[Station] = relationship(back_populates="balances")


class ForecastRisk(Base):
    """Fact: forecast-based risk assessment, one row per (station, issue_time, target_day).

    Composite PK enforces idempotency: re-issuing the same forecast for the same
    target date overwrites the previous row via ON CONFLICT DO UPDATE.
    BRIN on issued_at and target_date for efficient range queries.
    """

    __tablename__ = "forecast_risk"
    __table_args__ = (
        CheckConstraint(
            "et0_forecast_mm IS NULL OR et0_forecast_mm >= 0",
            name="ck_fr_et0_nonneg",
        ),
        CheckConstraint(
            "precip_forecast_mm IS NULL OR precip_forecast_mm >= 0",
            name="ck_fr_precip_nonneg",
        ),
        CheckConstraint(
            "vpd_forecast_kpa IS NULL OR vpd_forecast_kpa >= 0",
            name="ck_fr_vpd_nonneg",
        ),
        CheckConstraint(
            "dr_projected_mm IS NULL OR dr_projected_mm >= 0",
            name="ck_fr_dr_nonneg",
        ),
        Index("ix_fr_issued_at_brin", "issued_at", postgresql_using="brin"),
        Index("ix_fr_target_date_brin", "target_date", postgresql_using="brin"),
    )

    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    target_date: Mapped[_date] = mapped_column(Date, primary_key=True, nullable=False)
    risk_level: Mapped[Optional[str]] = mapped_column(_risk_level)
    et0_forecast_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    precip_forecast_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    vpd_forecast_kpa: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 3))
    dr_projected_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))

    station: Mapped[Station] = relationship(back_populates="forecasts")
