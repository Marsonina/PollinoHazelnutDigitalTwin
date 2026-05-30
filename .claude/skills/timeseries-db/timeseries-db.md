---
name: timeseries-db
description: PostgreSQL conventions for the agricultural telemetry store. Use when writing models, migrations, or queries for sensor readings, daily water balance, or forecast risk.
---
- stations(dim) ← sensors(dim) ← sensor_readings(fact, PK (sensor_id, ts)).
- daily_water_balance(PK station_id,date): et0_mm, kc, etc_mm, ks, precip_mm,
  irrigation_m3, irrigation_mm, taw_mm, raw_mm, dr_mm, vpd_kpa, risk_level enum.
- forecast_risk(PK station_id, issued_at, target_date).
- timestamptz only; NUMERIC(p,s) for measurements; CHECK ranges (et0 0–15, ks 0–1).
- BRIN index on ts/date; composite (sensor_id, ts DESC) for "latest reading".
- All writes idempotent via INSERT ... ON CONFLICT DO UPDATE.
- Plain PostgreSQL unless sub-minute multi-sensor load appears -> then TimescaleDB
  hypertable + continuous aggregates (justify the switch).
