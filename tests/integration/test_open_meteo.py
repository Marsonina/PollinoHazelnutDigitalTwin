"""Integration tests for the Open-Meteo forecast client.

All tests use the recorded fixture tests/fixtures/open_meteo_48h.json —
no live network calls are made.  HTTP error paths use unittest.mock.

Test inventory
--------------
Parsing (happy path):
  - 48 hourly entries returned
  - first / last timestamps parsed to datetime
  - peak temperature matches oracle (34.0 °C, day-1 14:00)
  - lat / lon echoed from response
  - all ET0 values non-negative
  - all precipitation values zero (dry heatwave fixture)
  - VPD exceeds 2 kPa threshold at peak (iron rule 3 observable)
  - all temperatures within physical range

Schema drift:
  - missing et0_fao_evapotranspiration → SchemaDriftError
  - missing vapour_pressure_deficit    → SchemaDriftError (iron rule 3 field)
  - multiple fields stripped at once   → SchemaDriftError
  - hourly key removed entirely        → SchemaDriftError

HTTP errors:
  - HTTP 500 → OpenMeteoError("HTTP 500 …")
  - HTTP 429 (rate limit) → OpenMeteoError("HTTP 429 …")

These tests MUST fail with ImportError until open_meteo.py is created.
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from pollino.ingest.weather.open_meteo import (
    ORCHARD_LAT,
    ORCHARD_LON,
    OpenMeteoError,
    SchemaDriftError,
    fetch_forecast,
    parse_response,
)

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "open_meteo_48h.json"


@pytest.fixture
def payload():
    """Return a fresh deep-copy of the recorded fixture for each test."""
    return json.loads(_FIXTURE.read_text())


# ---------------------------------------------------------------------------
# Parsing — happy path
# ---------------------------------------------------------------------------


def test_forecast_has_48_hourly_entries(payload):
    """48 h horizon → 48 list entries in every variable."""
    fc = parse_response(payload)
    assert len(fc.time) == 48
    for attr in (
        "et0_fao_evapotranspiration",
        "precipitation",
        "temperature_2m",
        "relative_humidity_2m",
        "wind_speed_10m",
        "shortwave_radiation",
        "vapour_pressure_deficit",
    ):
        assert len(getattr(fc, attr)) == 48, f"{attr} length != 48"


def test_first_timestamp_parsed_to_datetime(payload):
    """time[0] must be a timezone-naive datetime for 2025-07-15 00:00."""
    fc = parse_response(payload)
    assert isinstance(fc.time[0], datetime)
    assert fc.time[0] == datetime(2025, 7, 15, 0, 0)


def test_last_timestamp_is_horizon_end(payload):
    """time[47] must be 2025-07-16 23:00 — the 48 h horizon end."""
    fc = parse_response(payload)
    assert fc.time[47] == datetime(2025, 7, 16, 23, 0)


def test_peak_temperature_day1_oracle(payload):
    """Temperature at 14:00 day 1 must be the fixture oracle 34.0 °C."""
    fc = parse_response(payload)
    assert fc.temperature_2m[14] == pytest.approx(34.0, abs=0.2)


def test_lat_lon_echoed_from_response(payload):
    """Parsed forecast exposes the orchard coordinates from the response."""
    fc = parse_response(payload)
    assert fc.latitude == pytest.approx(ORCHARD_LAT, abs=0.001)
    assert fc.longitude == pytest.approx(ORCHARD_LON, abs=0.001)


def test_et0_all_non_negative(payload):
    """ET0 must be ≥ 0 for every hour (FAO-56 Eq.6 physical constraint)."""
    fc = parse_response(payload)
    assert all(
        v >= 0.0 for v in fc.et0_fao_evapotranspiration
    ), f"negative ET0 found: {min(fc.et0_fao_evapotranspiration)}"


def test_precipitation_all_zero_dry_spell(payload):
    """Fixture represents a dry heatwave — all precipitation values are 0."""
    fc = parse_response(payload)
    assert all(p == 0.0 for p in fc.precipitation)


def test_peak_vpd_exceeds_stress_threshold(payload):
    """Peak forecast VPD must exceed 2 kPa so iron-rule-3 channel can fire."""
    fc = parse_response(payload)
    assert (
        max(fc.vapour_pressure_deficit) > 2.0
    ), f"max VPD {max(fc.vapour_pressure_deficit):.3f} kPa does not exceed 2 kPa"


def test_temperatures_within_physical_range(payload):
    """All hourly temperatures must be within the physical bounds −10 … +55 °C."""
    fc = parse_response(payload)
    out = [t for t in fc.temperature_2m if not (-10 < t < 55)]
    assert out == [], f"out-of-range temperatures: {out}"


# ---------------------------------------------------------------------------
# Schema drift — API contract guard
# ---------------------------------------------------------------------------


def test_schema_drift_missing_et0(payload):
    """Dropped et0_fao_evapotranspiration field must raise SchemaDriftError."""
    del payload["hourly"]["et0_fao_evapotranspiration"]
    with pytest.raises(SchemaDriftError, match="et0_fao_evapotranspiration"):
        parse_response(payload)


def test_schema_drift_missing_vpd(payload):
    """Dropped vapour_pressure_deficit must raise SchemaDriftError.

    VPD is the independent heat-stress channel (iron rule 3); a silent drop
    would disable the RED alert without any error.
    """
    del payload["hourly"]["vapour_pressure_deficit"]
    with pytest.raises(SchemaDriftError, match="vapour_pressure_deficit"):
        parse_response(payload)


def test_schema_drift_multiple_missing_fields(payload):
    """Stripping several fields at once must still raise SchemaDriftError."""
    for field in (
        "et0_fao_evapotranspiration",
        "shortwave_radiation",
        "wind_speed_10m",
    ):
        del payload["hourly"][field]
    with pytest.raises(SchemaDriftError):
        parse_response(payload)


def test_schema_drift_hourly_key_absent(payload):
    """If the 'hourly' key is missing entirely, SchemaDriftError must fire."""
    del payload["hourly"]
    with pytest.raises(SchemaDriftError):
        parse_response(payload)


# ---------------------------------------------------------------------------
# HTTP error handling
# ---------------------------------------------------------------------------


def _mock_client(status: int, body: str = "") -> MagicMock:
    """Return a mock httpx.Client whose .get() raises HTTPStatusError."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.text = body or f"{status} Error"
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"{status}",
        request=MagicMock(spec=httpx.Request),
        response=response,
    )
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = response
    return client


def test_http_500_raises_open_meteo_error():
    """HTTP 500 from the API must raise OpenMeteoError(\"HTTP 500 …\")."""
    with pytest.raises(OpenMeteoError, match="HTTP 500"):
        fetch_forecast(client=_mock_client(500, "Internal Server Error"))


def test_http_429_raises_open_meteo_error():
    """HTTP 429 (rate-limited) must raise OpenMeteoError(\"HTTP 429 …\")."""
    with pytest.raises(OpenMeteoError, match="HTTP 429"):
        fetch_forecast(client=_mock_client(429, "Too Many Requests"))


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def _ok_client(fixture_payload: dict) -> MagicMock:
    """Return a mock httpx.Client that returns a valid 200 response."""
    response = MagicMock(spec=httpx.Response)
    response.raise_for_status.return_value = None
    response.json.return_value = fixture_payload
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = response
    return client


def test_cache_file_written_after_fetch(tmp_path, payload):
    """A cache file must exist on disk after the first successful fetch."""
    fetch_forecast(client=_ok_client(payload), cache_dir=tmp_path)
    cache_files = list(tmp_path.glob("forecast_*.json"))
    assert len(cache_files) == 1


def test_cache_hit_avoids_second_http_call(tmp_path, payload):
    """A second fetch within TTL must be served from cache — no HTTP call."""
    mock = _ok_client(payload)

    fetch_forecast(client=mock, cache_dir=tmp_path)
    assert mock.get.call_count == 1

    fetch_forecast(client=mock, cache_dir=tmp_path)
    assert mock.get.call_count == 1  # unchanged — cache hit


def test_stale_cache_triggers_refetch(tmp_path, payload):
    """An expired cache entry (ttl=0) must trigger a new HTTP call."""
    from datetime import timedelta

    mock = _ok_client(payload)
    fetch_forecast(client=mock, cache_dir=tmp_path, cache_ttl=timedelta(0))
    fetch_forecast(client=mock, cache_dir=tmp_path, cache_ttl=timedelta(0))
    assert mock.get.call_count == 2  # both fetches hit the network


def test_cache_returns_identical_forecast(tmp_path, payload):
    """Cached forecast must be identical to the directly parsed forecast."""
    mock = _ok_client(payload)

    first = fetch_forecast(client=mock, cache_dir=tmp_path)
    second = fetch_forecast(client=mock, cache_dir=tmp_path)  # from cache
    assert first.time == second.time
    assert first.vapour_pressure_deficit == second.vapour_pressure_deficit
    assert first.et0_fao_evapotranspiration == second.et0_fao_evapotranspiration


def test_different_params_use_separate_cache_entries(tmp_path, payload):
    """Different (lat, lon, hours) combinations must not share a cache slot."""
    mock = _ok_client(payload)
    fetch_forecast(ORCHARD_LAT, ORCHARD_LON, 48, client=mock, cache_dir=tmp_path)
    fetch_forecast(ORCHARD_LAT, ORCHARD_LON, 24, client=mock, cache_dir=tmp_path)
    cache_files = list(tmp_path.glob("forecast_*.json"))
    assert len(cache_files) == 2
    assert mock.get.call_count == 2
