"""Seed reference data for the Piana di Cammarata monitoring station.

Idempotent: safe to run multiple times.
  - Station upserted by unique code (ON CONFLICT DO UPDATE).
  - Sensors checked by (station_id, sensor_type, depth_cm) before insert;
    existing rows are left unchanged.

Run:
    DATABASE_URL=... uv run python -m pollino.db.seed
"""

from __future__ import annotations

import os
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from pollino.db.models import Sensor, Station

# ---------------------------------------------------------------------------
# Reference data — Piana di Cammarata, Castrovillari (CS), Calabria
# ---------------------------------------------------------------------------

_STATION: dict = {
    "code": "CAMMARATA_01",
    "name": "Piana di Cammarata — noccioleto Ferrara",
    "latitude": Decimal("39.812500"),
    "longitude": Decimal("16.205000"),
    "elevation_m": Decimal("381.0"),
}

# Sensor order matches the Netsense portal layout (air → soil).
# depth_cm is None for atmospheric sensors; 30 or 60 for soil probes.
_SENSORS: list[dict] = [
    {"sensor_type": "air_temp", "depth_cm": None, "unit": "°C"},
    {"sensor_type": "solar_rad", "depth_cm": None, "unit": "MJ/m²/day"},
    {"sensor_type": "wind_speed", "depth_cm": None, "unit": "m/s"},
    {"sensor_type": "soil_moisture", "depth_cm": 30, "unit": "m³/m³"},
    {"sensor_type": "soil_moisture", "depth_cm": 60, "unit": "m³/m³"},
]


def seed(session: Session) -> dict[str, object]:
    """Upsert the reference station and its sensors.

    Returns a summary dict: station_id, sensors_found, sensors_inserted.
    """
    # Upsert station by unique code — update metadata on re-run.
    stmt = (
        pg_insert(Station)
        .values(**_STATION)
        .on_conflict_do_update(
            index_elements=["code"],
            set_={
                "name": pg_insert(Station).excluded.name,
                "latitude": pg_insert(Station).excluded.latitude,
                "longitude": pg_insert(Station).excluded.longitude,
                "elevation_m": pg_insert(Station).excluded.elevation_m,
            },
        )
        .returning(Station.id)
    )
    station_id: int = session.execute(stmt).scalar_one()

    # Sensors have no unique DB constraint — check existence before inserting.
    inserted = 0
    for defn in _SENSORS:
        exists = (
            session.query(Sensor)
            .filter_by(
                station_id=station_id,
                sensor_type=defn["sensor_type"],
                depth_cm=defn["depth_cm"],
            )
            .first()
        )
        if not exists:
            session.add(Sensor(station_id=station_id, **defn))
            inserted += 1

    session.flush()
    found = len(_SENSORS) - inserted
    return {
        "station_id": station_id,
        "sensors_found": found,
        "sensors_inserted": inserted,
    }


if __name__ == "__main__":
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://pollino:pollino_dev@localhost:5432/pollino",
    )
    engine = create_engine(url)
    with Session(engine) as session:
        with session.begin():
            summary = seed(session)

    print(f"station_id   : {summary['station_id']}")
    print(f"sensors found: {summary['sensors_found']}")
    print(f"sensors added: {summary['sensors_inserted']}")
