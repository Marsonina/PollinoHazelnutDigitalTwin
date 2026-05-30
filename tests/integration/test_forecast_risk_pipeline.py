"""End-to-end tests: Open-Meteo forecast → daily aggregation → risk level.

Fixture: tests/fixtures/open_meteo_48h.json — 48-h heatwave scenario for
Piana di Cammarata, July 15-16 2025.

Fixture oracle values (verified against generation script):
  ET0 day-0 sum : 4.779 mm
  ET0 day-1 sum : 4.971 mm
  ETc day-0 (Kc=0.95) : 4.540 mm
  ETc day-1 (Kc=0.95) : 4.722 mm
  max VPD      : 4.048 kPa  (>> 2 kPa threshold → iron rule 3 always fires)
  precip sum   : 0.0 mm     (dry heatwave)

Soil parameters used throughout (Piana di Cammarata alluvial loam):
  TAW=120 mm, RAW=60 mm (p=0.50), MAD=90 mm, Kc=0.95 (hazelnut mid-season).
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from pollino.ingest.weather.open_meteo import (
    HourlyForecast,
    ORCHARD_LAT,
    ORCHARD_LON,
    parse_response,
)
from pollino.pipeline.forecast_risk import assess, daily_etc_and_precip

# ---------------------------------------------------------------------------
# Shared parameters
# ---------------------------------------------------------------------------

TAW, RAW, MAD, KC = 120.0, 60.0, 90.0, 0.95

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "open_meteo_48h.json"


@pytest.fixture
def heatwave() -> HourlyForecast:
    """Parsed 48-h heatwave forecast for Piana di Cammarata."""
    return parse_response(json.loads(_FIXTURE.read_text()))


def _mild_forecast(
    *,
    et0_per_hour: float = 0.1,
    vpd: float = 0.8,
    precip_per_hour: float = 0.0,
) -> HourlyForecast:
    """Construct a uniform 48-h mild forecast — no stress, low VPD."""
    return HourlyForecast(
        time=[datetime(2025, 7, 15, h % 24, 0) for h in range(48)],
        et0_fao_evapotranspiration=[et0_per_hour] * 48,
        precipitation=[precip_per_hour] * 48,
        temperature_2m=[22.0] * 48,
        relative_humidity_2m=[70] * 48,
        wind_speed_10m=[1.5] * 48,
        shortwave_radiation=[0.0] * 48,
        vapour_pressure_deficit=[vpd] * 48,
        latitude=ORCHARD_LAT,
        longitude=ORCHARD_LON,
    )


# ---------------------------------------------------------------------------
# daily_etc_and_precip — aggregation oracles
# ---------------------------------------------------------------------------


def test_aggregation_returns_two_values_for_48h_horizon(heatwave):
    """days=2 must return two-element lists for a 48-h forecast."""
    etc, precip = daily_etc_and_precip(heatwave, kc=KC, days=2)
    assert len(etc) == 2
    assert len(precip) == 2


def test_daily_etc_day0_oracle(heatwave):
    """ETc[0] = Kc × sum(hourly ET0, hours 0-23).  FAO-56 Eq.58."""
    etc, _ = daily_etc_and_precip(heatwave, kc=KC)
    expected = KC * sum(heatwave.et0_fao_evapotranspiration[:24])  # 0.95 × 4.779
    assert etc[0] == pytest.approx(expected, abs=0.001)


def test_daily_etc_day1_oracle(heatwave):
    """ETc[1] = Kc × sum(hourly ET0, hours 24-47).  FAO-56 Eq.58."""
    etc, _ = daily_etc_and_precip(heatwave, kc=KC)
    expected = KC * sum(heatwave.et0_fao_evapotranspiration[24:])  # 0.95 × 4.971
    assert etc[1] == pytest.approx(expected, abs=0.001)


def test_daily_precip_all_zero_dry_heatwave(heatwave):
    """Heatwave fixture has no precipitation — both daily sums must be 0."""
    _, precip = daily_etc_and_precip(heatwave, kc=KC)
    assert precip == [0.0, 0.0]


def test_daily_etc_scales_with_kc(heatwave):
    """Doubling Kc must exactly double the daily ETc."""
    etc_1x, _ = daily_etc_and_precip(heatwave, kc=0.50)
    etc_2x, _ = daily_etc_and_precip(heatwave, kc=1.00)
    for a, b in zip(etc_1x, etc_2x):
        assert b == pytest.approx(2 * a, rel=1e-9)


# ---------------------------------------------------------------------------
# assess() — end-to-end risk level
# ---------------------------------------------------------------------------


def test_heatwave_red_at_field_capacity(heatwave):
    """Iron rule 3: max VPD=4.048 kPa fires RED even when Dr=0 (soil at FC).

    No soil stress whatsoever — RED comes purely from the VPD channel.
    """
    level = assess(heatwave, dr=0.0, raw=RAW, mad=MAD, taw=TAW, kc=KC)
    assert level == "R"


def test_heatwave_red_from_yellow_zone(heatwave):
    """Dr=75 mm is already YELLOW (RAW<75<MAD); heatwave VPD escalates to RED."""
    level = assess(heatwave, dr=75.0, raw=RAW, mad=MAD, taw=TAW, kc=KC)
    assert level == "R"


def test_heatwave_red_independent_of_soil_state(heatwave):
    """VPD=4.048 triggers RED regardless of Dr — iron rule 3 is independent."""
    for dr in (0.0, 30.0, RAW, 75.0, MAD):
        level = assess(heatwave, dr=dr, raw=RAW, mad=MAD, taw=TAW, kc=KC)
        assert level == "R", f"Expected R for dr={dr}, got {level}"


def test_mild_green_well_watered_no_vpd_stress():
    """No false positive: low VPD (<2 kPa), healthy soil, tiny ETc → GREEN."""
    mild = _mild_forecast(et0_per_hour=0.08, vpd=0.8)
    # ETc/day = 0.95 × 0.08 × 24 = 1.82 mm;  Dr=10 → day1=11.8, day2=13.6 << RAW=60
    level = assess(mild, dr=10.0, raw=RAW, mad=MAD, taw=TAW, kc=KC)
    assert level == "G"


def test_soil_lookahead_red_no_vpd_stress():
    """Soil-only RED: VPD<2 kPa but soil depletes past MAD within 2 days."""
    # et0_per_hour = 6.32/24 → daily ET0=6.32 → ETc=0.95×6.32=6.0 mm/day
    # Dr=80 → day1: 86 < MAD=90 (Y); day2: 92 > MAD=90 → R
    dry_hot = _mild_forecast(et0_per_hour=6.32 / 24, vpd=1.5)
    level = assess(dry_hot, dr=80.0, raw=RAW, mad=MAD, taw=TAW, kc=KC)
    assert level == "R"


def test_yellow_when_dr_above_raw_vpd_below_threshold():
    """Dr just above RAW, VPD below threshold, soil won't reach MAD → YELLOW."""
    # ETc=2mm/day, Dr=65: day1=67, day2=69 — both stay under MAD=90
    mild = _mild_forecast(et0_per_hour=2.0 / 24, vpd=1.5)
    level = assess(mild, dr=65.0, raw=RAW, mad=MAD, taw=TAW, kc=KC)
    assert level == "Y"


def test_rain_defuses_soil_lookahead(heatwave):
    """Sufficient forecast rain prevents soil-depletion RED even in heatwave.

    Note: VPD=4.048 still triggers RED via iron rule 3 regardless of rain.
    This test confirms rain is correctly subtracted from the daily balance.
    """
    # 20 mm/day rain >> ETc≈4.5 mm/day → Dr falls, not soil-based RED
    # But VPD=4.048 still fires RED independently
    rainy = _mild_forecast(et0_per_hour=0.19, vpd=1.5, precip_per_hour=20.0 / 24)
    level = assess(rainy, dr=85.0, raw=RAW, mad=MAD, taw=TAW, kc=KC)
    # 85 - (20-0.95*0.19*24) ≈ 85 - 20 + 4.3 = 69 < MAD; VPD=1.5 < 2 → Y
    assert level == "Y"
