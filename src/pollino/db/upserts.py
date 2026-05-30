"""Idempotent upserts for the telemetry store.

Every write uses INSERT … ON CONFLICT DO UPDATE so the pipeline can be re-run
safely without producing duplicates.  Conflict keys match the composite PKs
defined in models.py and the timeseries-db skill.
"""

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from pollino.db.models import DailyWaterBalance, SensorReading

# Columns updated when a sensor_readings row already exists.
_READING_UPDATE = ("value",)

# Measurement columns updated when a daily_water_balance row already exists.
# computed_at is refreshed to mark when the balance was last re-computed.
_BALANCE_UPDATE = (
    "et0_mm",
    "kc",
    "etc_mm",
    "ks",
    "precip_mm",
    "irrigation_m3",
    "irrigation_mm",
    "taw_mm",
    "raw_mm",
    "dr_mm",
    "vpd_kpa",
    "risk_level",
)


def upsert_sensor_readings(session: Session, rows: list[dict]) -> int:
    """Bulk upsert sensor readings.  Conflict key: (sensor_id, ts).

    On conflict the value is overwritten; all other fields are immutable
    (sensor_id and ts are the key).

    Parameters
    ----------
    rows : list of dicts with keys sensor_id, ts, value.

    Returns
    -------
    Number of rows inserted or updated (PostgreSQL rowcount for DO UPDATE
    counts every affected row, including no-ops when the value is unchanged).
    """
    if not rows:
        return 0
    stmt = pg_insert(SensorReading).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["sensor_id", "ts"],
        set_={col: getattr(stmt.excluded, col) for col in _READING_UPDATE},
    )
    result = session.execute(stmt)
    return result.rowcount


def upsert_daily_water_balance(session: Session, rows: list[dict]) -> int:
    """Bulk upsert daily water balance rows.  Conflict key: (station_id, date).

    On conflict all measurement columns are overwritten with the new values
    and computed_at is refreshed to now().

    Parameters
    ----------
    rows : list of dicts whose keys are a subset of DailyWaterBalance columns.

    Returns
    -------
    Number of rows inserted or updated.
    """
    if not rows:
        return 0
    stmt = pg_insert(DailyWaterBalance).values(rows)
    update_set = {col: getattr(stmt.excluded, col) for col in _BALANCE_UPDATE}
    update_set["computed_at"] = func.now()
    stmt = stmt.on_conflict_do_update(
        index_elements=["station_id", "date"],
        set_=update_set,
    )
    result = session.execute(stmt)
    return result.rowcount
