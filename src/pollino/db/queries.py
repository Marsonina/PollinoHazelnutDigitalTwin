"""Read-only query functions for the telemetry store.

Both functions are designed to hit the purpose-built indexes:
  latest_reading_per_sensor — (sensor_id, ts DESC) composite B-tree
  dr_trend                  — BRIN index on daily_water_balance.date
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from pollino.db.models import DailyWaterBalance, Sensor, SensorReading


def latest_reading_per_sensor(
    session: Session,
    station_id: int,
) -> list[SensorReading]:
    """Return the single most-recent reading for every sensor at a station.

    Uses PostgreSQL DISTINCT ON (sensor_id) with ORDER BY sensor_id, ts DESC
    so the query is satisfied entirely by the (sensor_id, ts DESC) B-tree
    index — no sort or sequential scan needed.
    """
    stmt = (
        select(SensorReading)
        .join(SensorReading.sensor)
        .where(Sensor.station_id == station_id)
        .distinct(SensorReading.sensor_id)  # DISTINCT ON (sensor_id)
        .order_by(SensorReading.sensor_id, SensorReading.ts.desc())
    )
    return list(session.scalars(stmt))


def dr_trend(
    session: Session,
    station_id: int,
    days: int = 7,
    *,
    reference_date: date | None = None,
) -> list[DailyWaterBalance]:
    """Return the last *days* daily-balance rows for a station, oldest first.

    The date window is [reference_date − (days−1), reference_date] inclusive.
    reference_date defaults to today; pass an explicit value in tests or
    back-fills so results are deterministic.

    The BRIN index on daily_water_balance.date keeps range-scan cost near O(1)
    for the append-only season data.
    """
    ref = reference_date or date.today()
    since = ref - timedelta(days=days - 1)
    stmt = (
        select(DailyWaterBalance)
        .where(DailyWaterBalance.station_id == station_id)
        .where(DailyWaterBalance.date >= since)
        .where(DailyWaterBalance.date <= ref)
        .order_by(DailyWaterBalance.date)
    )
    return list(session.scalars(stmt))
