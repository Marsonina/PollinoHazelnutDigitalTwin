"""Integration tests for idempotent upserts.

Each test inserts a batch, re-inserts the same batch with changed values,
then asserts: (a) row count is unchanged, (b) values reflect the latest write.

All writes happen inside a transaction that is rolled back by the db_session
fixture — no permanent data is left in the database.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4


from pollino.db.models import DailyWaterBalance, Sensor, SensorReading, Station
from pollino.db.upserts import upsert_daily_water_balance, upsert_sensor_readings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_station(session, suffix: str = "") -> Station:
    station = Station(code=f"TST_{uuid4().hex[:8]}{suffix}", name="Test Station")
    session.add(station)
    session.flush()
    return station


def _make_sensor(session, station_id: int, sensor_type: str = "air_temp") -> Sensor:
    sensor = Sensor(station_id=station_id, sensor_type=sensor_type, unit="°C")
    session.add(sensor)
    session.flush()
    return sensor


def _ts(hour: int) -> datetime:
    return datetime(2025, 7, 15, hour, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# sensor_readings upserts
# ---------------------------------------------------------------------------


def test_sensor_reading_upsert_inserts_new_row(db_session):
    """First upsert creates a row."""
    station = _make_station(db_session)
    sensor = _make_sensor(db_session, station.id)

    upsert_sensor_readings(
        db_session,
        [{"sensor_id": sensor.id, "ts": _ts(12), "value": Decimal("25.50")}],
    )

    assert db_session.query(SensorReading).filter_by(sensor_id=sensor.id).count() == 1


def test_sensor_reading_upsert_no_duplicate_on_second_write(db_session):
    """Re-inserting the same (sensor_id, ts) does not create a second row."""
    station = _make_station(db_session)
    sensor = _make_sensor(db_session, station.id)
    ts = _ts(12)

    upsert_sensor_readings(
        db_session, [{"sensor_id": sensor.id, "ts": ts, "value": Decimal("25.50")}]
    )
    upsert_sensor_readings(
        db_session, [{"sensor_id": sensor.id, "ts": ts, "value": Decimal("26.00")}]
    )

    rows = db_session.query(SensorReading).filter_by(sensor_id=sensor.id).all()
    assert len(rows) == 1


def test_sensor_reading_upsert_value_updated(db_session):
    """Second upsert with a different value overwrites the original."""
    station = _make_station(db_session)
    sensor = _make_sensor(db_session, station.id)
    ts = _ts(12)

    upsert_sensor_readings(
        db_session, [{"sensor_id": sensor.id, "ts": ts, "value": Decimal("25.50")}]
    )
    upsert_sensor_readings(
        db_session, [{"sensor_id": sensor.id, "ts": ts, "value": Decimal("26.00")}]
    )

    row = db_session.query(SensorReading).filter_by(sensor_id=sensor.id, ts=ts).one()
    assert row.value == Decimal("26.00")


def test_sensor_reading_upsert_multi_row_batch(db_session):
    """A batch of three readings upserted twice yields exactly three rows with updated values."""
    station = _make_station(db_session)
    sensor = _make_sensor(db_session, station.id)

    batch_v1 = [
        {"sensor_id": sensor.id, "ts": _ts(h), "value": Decimal(f"{20 + h}.00")}
        for h in range(3)
    ]
    batch_v2 = [
        {"sensor_id": sensor.id, "ts": _ts(h), "value": Decimal(f"{30 + h}.00")}
        for h in range(3)
    ]

    upsert_sensor_readings(db_session, batch_v1)
    upsert_sensor_readings(db_session, batch_v2)

    rows = (
        db_session.query(SensorReading)
        .filter_by(sensor_id=sensor.id)
        .order_by(SensorReading.ts)
        .all()
    )
    assert len(rows) == 3
    assert [r.value for r in rows] == [Decimal(f"{30 + h}.00") for h in range(3)]


def test_sensor_reading_upsert_empty_batch_is_noop(db_session):
    """Upserting an empty list must not raise and returns 0."""
    assert upsert_sensor_readings(db_session, []) == 0


# ---------------------------------------------------------------------------
# daily_water_balance upserts
# ---------------------------------------------------------------------------


def test_daily_balance_upsert_inserts_new_row(db_session):
    """First upsert creates a balance row."""
    station = _make_station(db_session)

    upsert_daily_water_balance(
        db_session,
        [
            {
                "station_id": station.id,
                "date": date(2025, 7, 15),
                "et0_mm": Decimal("5.10"),
                "risk_level": "G",
            }
        ],
    )

    assert (
        db_session.query(DailyWaterBalance).filter_by(station_id=station.id).count()
        == 1
    )


def test_daily_balance_upsert_no_duplicate_on_second_write(db_session):
    """Re-inserting the same (station_id, date) does not create a second row."""
    station = _make_station(db_session)
    d = date(2025, 7, 15)

    upsert_daily_water_balance(
        db_session,
        [
            {
                "station_id": station.id,
                "date": d,
                "et0_mm": Decimal("5.10"),
                "risk_level": "G",
            }
        ],
    )
    upsert_daily_water_balance(
        db_session,
        [
            {
                "station_id": station.id,
                "date": d,
                "et0_mm": Decimal("6.70"),
                "risk_level": "R",
            }
        ],
    )

    rows = db_session.query(DailyWaterBalance).filter_by(station_id=station.id).all()
    assert len(rows) == 1


def test_daily_balance_upsert_all_columns_updated(db_session):
    """Second upsert overwrites every measurement column and escalates risk."""
    station = _make_station(db_session)
    d = date(2025, 7, 15)

    batch_v1 = [
        {
            "station_id": station.id,
            "date": d,
            "et0_mm": Decimal("5.10"),
            "kc": Decimal("0.950"),
            "etc_mm": Decimal("4.845"),
            "ks": Decimal("1.000"),
            "dr_mm": Decimal("40.00"),
            "vpd_kpa": Decimal("1.500"),
            "risk_level": "G",
        }
    ]
    batch_v2 = [
        {
            "station_id": station.id,
            "date": d,
            "et0_mm": Decimal("6.70"),  # updated — heatwave
            "kc": Decimal("0.950"),
            "etc_mm": Decimal("6.365"),
            "ks": Decimal("0.500"),  # updated — stress
            "dr_mm": Decimal("75.00"),  # updated — deeper deficit
            "vpd_kpa": Decimal("2.800"),  # updated — VPD spike
            "risk_level": "R",  # escalated
        }
    ]

    upsert_daily_water_balance(db_session, batch_v1)
    upsert_daily_water_balance(db_session, batch_v2)

    row = (
        db_session.query(DailyWaterBalance)
        .filter_by(station_id=station.id, date=d)
        .one()
    )
    assert row.et0_mm == Decimal("6.70")
    assert row.ks == Decimal("0.500")
    assert row.dr_mm == Decimal("75.00")
    assert row.vpd_kpa == Decimal("2.800")
    assert row.risk_level == "R"


def test_daily_balance_upsert_multi_day_batch(db_session):
    """A three-day batch upserted twice yields exactly three rows with updated values."""
    station = _make_station(db_session)
    dates = [date(2025, 7, d) for d in range(15, 18)]

    batch_v1 = [
        {
            "station_id": station.id,
            "date": d,
            "et0_mm": Decimal("5.00"),
            "risk_level": "G",
        }
        for d in dates
    ]
    batch_v2 = [
        {
            "station_id": station.id,
            "date": d,
            "et0_mm": Decimal("7.00"),
            "risk_level": "R",
        }
        for d in dates
    ]

    upsert_daily_water_balance(db_session, batch_v1)
    upsert_daily_water_balance(db_session, batch_v2)

    rows = (
        db_session.query(DailyWaterBalance)
        .filter_by(station_id=station.id)
        .order_by(DailyWaterBalance.date)
        .all()
    )
    assert len(rows) == 3
    assert all(r.et0_mm == Decimal("7.00") for r in rows)
    assert all(r.risk_level == "R" for r in rows)


def test_daily_balance_upsert_empty_batch_is_noop(db_session):
    """Upserting an empty list must not raise and returns 0."""
    assert upsert_daily_water_balance(db_session, []) == 0
