"""Initial schema: stations, sensors, sensor_readings, daily_water_balance, forecast_risk.

Revision ID: 0001
Revises:
Create Date: 2026-05-29

Tables created:
  stations              — dimension: physical monitoring stations
  sensors               — dimension: sensors attached to stations
  sensor_readings       — fact (narrow): one row per sensor per timestep
  daily_water_balance   — fact (wide): computed daily agronomy balance per station
  forecast_risk         — fact: forecast-based risk assessment

Indexes:
  BRIN   on sensor_readings.ts        (append-only time-series range scans)
  BRIN   on daily_water_balance.date
  BRIN   on forecast_risk.issued_at
  BRIN   on forecast_risk.target_date
  B-tree on sensors.station_id        (FK lookup)
  B-tree on sensor_readings(sensor_id, ts DESC)  (latest-reading query)
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Reusable reference to the risk_level enum (type already created in upgrade).
_risk_level = postgresql.ENUM("G", "Y", "R", name="risk_level", create_type=False)


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 0. PostgreSQL ENUM type — must precede any table that uses it.      #
    # ------------------------------------------------------------------ #
    op.execute("CREATE TYPE risk_level AS ENUM ('G', 'Y', 'R')")

    # ------------------------------------------------------------------ #
    # 1. stations (dimension)                                              #
    # ------------------------------------------------------------------ #
    op.create_table(
        "stations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("elevation_m", sa.Numeric(7, 1), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_stations_code"),
    )

    # ------------------------------------------------------------------ #
    # 2. sensors (dimension)                                               #
    # ------------------------------------------------------------------ #
    op.create_table(
        "sensors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("sensor_type", sa.String(50), nullable=False),
        sa.Column("depth_cm", sa.SmallInteger(), nullable=True),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sensors_station_id", "sensors", ["station_id"])

    # ------------------------------------------------------------------ #
    # 3. sensor_readings (narrow fact, PK: sensor_id, ts)                 #
    # ------------------------------------------------------------------ #
    op.create_table(
        "sensor_readings",
        sa.Column("sensor_id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Numeric(12, 4), nullable=False),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensors.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("sensor_id", "ts"),
    )
    # BRIN: tiny index, optimal for append-only time-series range scans.
    op.create_index(
        "ix_sensor_readings_ts_brin",
        "sensor_readings",
        ["ts"],
        postgresql_using="brin",
    )
    # Composite descending: fast "latest reading for sensor X" query.
    op.execute(
        "CREATE INDEX ix_sensor_readings_sensor_ts_desc "
        "ON sensor_readings (sensor_id, ts DESC)"
    )

    # ------------------------------------------------------------------ #
    # 4. daily_water_balance (wide fact, PK: station_id, date)            #
    # ------------------------------------------------------------------ #
    op.create_table(
        "daily_water_balance",
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("et0_mm", sa.Numeric(5, 2), nullable=True),
        sa.Column("kc", sa.Numeric(4, 3), nullable=True),
        sa.Column("etc_mm", sa.Numeric(5, 2), nullable=True),
        sa.Column("ks", sa.Numeric(4, 3), nullable=True),
        sa.Column("precip_mm", sa.Numeric(6, 2), nullable=True),
        sa.Column("irrigation_m3", sa.Numeric(8, 2), nullable=True),
        sa.Column("irrigation_mm", sa.Numeric(6, 2), nullable=True),
        sa.Column("taw_mm", sa.Numeric(6, 2), nullable=True),
        sa.Column("raw_mm", sa.Numeric(6, 2), nullable=True),
        sa.Column("dr_mm", sa.Numeric(6, 2), nullable=True),
        sa.Column("vpd_kpa", sa.Numeric(5, 3), nullable=True),
        sa.Column("risk_level", _risk_level, nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "et0_mm IS NULL OR (et0_mm >= 0 AND et0_mm <= 15)",
            name="ck_dwb_et0_range",
        ),
        sa.CheckConstraint(
            "ks IS NULL OR (ks >= 0 AND ks <= 1)",
            name="ck_dwb_ks_range",
        ),
        sa.CheckConstraint(
            "etc_mm IS NULL OR etc_mm >= 0",
            name="ck_dwb_etc_nonneg",
        ),
        sa.CheckConstraint(
            "precip_mm IS NULL OR precip_mm >= 0",
            name="ck_dwb_precip_nonneg",
        ),
        sa.CheckConstraint(
            "irrigation_m3 IS NULL OR irrigation_m3 >= 0",
            name="ck_dwb_irrig_m3_nonneg",
        ),
        sa.CheckConstraint(
            "irrigation_mm IS NULL OR irrigation_mm >= 0",
            name="ck_dwb_irrig_mm_nonneg",
        ),
        sa.CheckConstraint(
            "taw_mm IS NULL OR taw_mm > 0",
            name="ck_dwb_taw_pos",
        ),
        sa.CheckConstraint(
            "raw_mm IS NULL OR raw_mm >= 0",
            name="ck_dwb_raw_nonneg",
        ),
        sa.CheckConstraint(
            "dr_mm IS NULL OR dr_mm >= 0",
            name="ck_dwb_dr_nonneg",
        ),
        sa.CheckConstraint(
            "vpd_kpa IS NULL OR vpd_kpa >= 0",
            name="ck_dwb_vpd_nonneg",
        ),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("station_id", "date"),
    )
    op.create_index(
        "ix_dwb_date_brin",
        "daily_water_balance",
        ["date"],
        postgresql_using="brin",
    )

    # ------------------------------------------------------------------ #
    # 5. forecast_risk (fact, PK: station_id, issued_at, target_date)     #
    # ------------------------------------------------------------------ #
    op.create_table(
        "forecast_risk",
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("risk_level", _risk_level, nullable=True),
        sa.Column("et0_forecast_mm", sa.Numeric(5, 2), nullable=True),
        sa.Column("precip_forecast_mm", sa.Numeric(6, 2), nullable=True),
        sa.Column("vpd_forecast_kpa", sa.Numeric(5, 3), nullable=True),
        sa.Column("dr_projected_mm", sa.Numeric(6, 2), nullable=True),
        sa.CheckConstraint(
            "et0_forecast_mm IS NULL OR et0_forecast_mm >= 0",
            name="ck_fr_et0_nonneg",
        ),
        sa.CheckConstraint(
            "precip_forecast_mm IS NULL OR precip_forecast_mm >= 0",
            name="ck_fr_precip_nonneg",
        ),
        sa.CheckConstraint(
            "vpd_forecast_kpa IS NULL OR vpd_forecast_kpa >= 0",
            name="ck_fr_vpd_nonneg",
        ),
        sa.CheckConstraint(
            "dr_projected_mm IS NULL OR dr_projected_mm >= 0",
            name="ck_fr_dr_nonneg",
        ),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("station_id", "issued_at", "target_date"),
    )
    op.create_index(
        "ix_fr_issued_at_brin",
        "forecast_risk",
        ["issued_at"],
        postgresql_using="brin",
    )
    op.create_index(
        "ix_fr_target_date_brin",
        "forecast_risk",
        ["target_date"],
        postgresql_using="brin",
    )


def downgrade() -> None:
    # Drop in reverse dependency order; indexes are dropped with their tables.
    op.drop_table("forecast_risk")
    op.drop_table("daily_water_balance")
    op.execute("DROP INDEX IF EXISTS ix_sensor_readings_sensor_ts_desc")
    op.drop_table("sensor_readings")
    op.drop_table("sensors")
    op.drop_table("stations")
    op.execute("DROP TYPE risk_level")
