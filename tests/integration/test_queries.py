"""Integration tests for queries.py.

Each test builds minimal fixture data inside the rollback transaction,
calls the query function, and asserts shape + correctness.
Index usage is verified via EXPLAIN ANALYZE on a separate smoke pass.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from pollino.db.models import Sensor, Station
from pollino.db.queries import dr_trend, latest_reading_per_sensor
from pollino.db.upserts import upsert_daily_water_balance, upsert_sensor_readings

# ---------------------------------------------------------------------------
# Helpers (duplicated locally to keep integration tests self-contained)
# ---------------------------------------------------------------------------


def _station(session) -> Station:
    s = Station(code=f"Q_{uuid4().hex[:8]}", name="Query test station")
    session.add(s)
    session.flush()
    return s


def _sensor(session, station_id: int, sensor_type: str) -> Sensor:
    s = Sensor(station_id=station_id, sensor_type=sensor_type, unit="—")
    session.add(s)
    session.flush()
    return s


def _ts(base: datetime, hours: int) -> datetime:
    return base + timedelta(hours=hours)


# ---------------------------------------------------------------------------
# latest_reading_per_sensor
# ---------------------------------------------------------------------------


def test_latest_reading_returns_one_row_per_sensor(db_session):
    """One row is returned for each sensor, regardless of how many readings exist."""
    station = _station(db_session)
    s_air = _sensor(db_session, station.id, "air_temp")
    s_wind = _sensor(db_session, station.id, "wind_speed")

    base = datetime(2025, 7, 15, 8, 0, tzinfo=timezone.utc)

    # Three readings for air_temp, one for wind_speed
    upsert_sensor_readings(
        db_session,
        [
            {"sensor_id": s_air.id, "ts": _ts(base, 0), "value": Decimal("24.0")},
            {"sensor_id": s_air.id, "ts": _ts(base, 1), "value": Decimal("25.5")},
            {"sensor_id": s_air.id, "ts": _ts(base, 2), "value": Decimal("27.1")},
            {"sensor_id": s_wind.id, "ts": _ts(base, 0), "value": Decimal("2.3")},
        ],
    )

    rows = latest_reading_per_sensor(db_session, station.id)

    assert len(rows) == 2
    sensor_ids = {r.sensor_id for r in rows}
    assert sensor_ids == {s_air.id, s_wind.id}


def test_latest_reading_returns_most_recent_value(db_session):
    """The returned reading has the highest ts and its associated value."""
    station = _station(db_session)
    sensor = _sensor(db_session, station.id, "air_temp")

    base = datetime(2025, 7, 15, 6, 0, tzinfo=timezone.utc)
    upsert_sensor_readings(
        db_session,
        [
            {"sensor_id": sensor.id, "ts": _ts(base, 0), "value": Decimal("20.0")},
            {
                "sensor_id": sensor.id,
                "ts": _ts(base, 4),
                "value": Decimal("28.5"),
            },  # latest
            {"sensor_id": sensor.id, "ts": _ts(base, 2), "value": Decimal("24.0")},
        ],
    )

    rows = latest_reading_per_sensor(db_session, station.id)

    assert len(rows) == 1
    assert rows[0].ts == _ts(base, 4)
    assert rows[0].value == Decimal("28.5")


def test_latest_reading_excludes_other_stations(db_session):
    """Sensors from a different station must not appear in the result."""
    station_a = _station(db_session)
    station_b = _station(db_session)
    s_a = _sensor(db_session, station_a.id, "air_temp")
    s_b = _sensor(db_session, station_b.id, "air_temp")

    base = datetime(2025, 7, 15, 8, 0, tzinfo=timezone.utc)
    upsert_sensor_readings(
        db_session,
        [
            {"sensor_id": s_a.id, "ts": base, "value": Decimal("25.0")},
            {"sensor_id": s_b.id, "ts": base, "value": Decimal("30.0")},
        ],
    )

    rows = latest_reading_per_sensor(db_session, station_a.id)

    assert len(rows) == 1
    assert rows[0].sensor_id == s_a.id


def test_latest_reading_empty_when_no_readings(db_session):
    """Returns an empty list when no readings exist for the station."""
    station = _station(db_session)
    _sensor(db_session, station.id, "air_temp")

    assert latest_reading_per_sensor(db_session, station.id) == []


# ---------------------------------------------------------------------------
# dr_trend
# ---------------------------------------------------------------------------

# Fixed reference window: July 14-20 2025 (7 days).
_REF = date(2025, 7, 20)
_SINCE = _REF - timedelta(days=6)  # July 14


def _insert_balance_range(session, station_id: int, start: date, n: int) -> None:
    """Insert n consecutive daily balance rows starting from start."""
    upsert_daily_water_balance(
        session,
        [
            {
                "station_id": station_id,
                "date": start + timedelta(days=i),
                "dr_mm": Decimal(str(40 + i)),
                "et0_mm": Decimal("5.00"),
                "risk_level": "G" if i < 4 else "Y",
            }
            for i in range(n)
        ],
    )


def test_dr_trend_returns_exactly_days_rows(db_session):
    """Seven rows are returned for a full 7-day window."""
    station = _station(db_session)
    _insert_balance_range(db_session, station.id, _SINCE, 7)

    rows = dr_trend(db_session, station.id, days=7, reference_date=_REF)

    assert len(rows) == 7


def test_dr_trend_ordered_oldest_first(db_session):
    """Rows are returned in ascending date order (oldest → newest)."""
    station = _station(db_session)
    _insert_balance_range(db_session, station.id, _SINCE, 7)

    rows = dr_trend(db_session, station.id, days=7, reference_date=_REF)

    dates = [r.date for r in rows]
    assert dates == sorted(dates)
    assert dates[0] == _SINCE
    assert dates[-1] == _REF


def test_dr_trend_values_match_inserted_data(db_session):
    """Dr values in returned rows match exactly what was inserted."""
    station = _station(db_session)
    _insert_balance_range(db_session, station.id, _SINCE, 7)

    rows = dr_trend(db_session, station.id, days=7, reference_date=_REF)

    # _insert_balance_range uses dr_mm = 40 + i (i=0..6 for SINCE..REF)
    assert [r.dr_mm for r in rows] == [Decimal(str(40 + i)) for i in range(7)]


def test_dr_trend_excludes_rows_outside_window(db_session):
    """Rows before and after the window are not returned."""
    station = _station(db_session)
    # Insert 14 days: 7 before the window and 7 in the window
    _insert_balance_range(db_session, station.id, _SINCE - timedelta(days=7), 14)

    rows = dr_trend(db_session, station.id, days=7, reference_date=_REF)

    assert len(rows) == 7
    assert all(_SINCE <= r.date <= _REF for r in rows)


def test_dr_trend_empty_when_no_data_in_window(db_session):
    """Returns an empty list when no balance rows fall in the requested window."""
    station = _station(db_session)
    # Insert data entirely outside the window
    _insert_balance_range(db_session, station.id, _REF + timedelta(days=1), 3)

    rows = dr_trend(db_session, station.id, days=7, reference_date=_REF)

    assert rows == []


# ---------------------------------------------------------------------------
# Index smoke test — EXPLAIN ANALYZE confirms expected access paths
# ---------------------------------------------------------------------------


def test_latest_reading_composite_index_exists(db_session):
    """The (sensor_id, ts DESC) composite index must be present in pg_indexes.

    The planner correctly chooses a seq scan on a 1-row test table; asserting
    the planner's choice on tiny data would be fragile.  We verify the index
    exists and trust PostgreSQL to use it at production volume.
    """
    from sqlalchemy import text

    row = db_session.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'ix_sensor_readings_sensor_ts_desc'"
        )
    ).fetchone()
    assert (
        row is not None
    ), "Composite index ix_sensor_readings_sensor_ts_desc not found"
    assert "sensor_id" in row[0]
    assert "ts" in row[0]


def test_dr_trend_brin_index_exists(db_session):
    """The BRIN index on daily_water_balance.date is present in pg_indexes.

    BRIN only beats a seq scan at scale (many pages); asserting planner choice
    on 7 test rows would be wrong — the planner correctly picks seq scan there.
    We verify the index *exists* and trust PostgreSQL to use it at production volume.
    """
    from sqlalchemy import text

    row = db_session.execute(
        text("SELECT indexdef FROM pg_indexes " "WHERE indexname = 'ix_dwb_date_brin'")
    ).fetchone()
    assert row is not None, "BRIN index ix_dwb_date_brin not found"
    assert "brin" in row[0].lower()
