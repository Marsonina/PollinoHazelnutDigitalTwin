"""Open-Meteo forecast client — httpx, file-based response cache, no API key.

Cache behaviour
---------------
Responses are cached as JSON files under OPEN_METEO_CACHE_DIR
(default: ~/.cache/pollino/open_meteo/).  Default TTL is 1 h; override via
OPEN_METEO_CACHE_TTL_SECONDS.  Pass cache_dir=path in tests to isolate to a
tmp directory; pass cache_ttl=timedelta(0) to force a re-fetch.

Writes use a temp-file + atomic rename so a crash mid-write never leaves a
corrupt cache entry.

Never fails silently: HTTP errors and missing fields raise typed exceptions
so the pipeline alerting path is always triggered (iron rule 4).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Orchard coordinates
# ---------------------------------------------------------------------------

ORCHARD_LAT: float = 39.8125
ORCHARD_LON: float = 16.205

# ---------------------------------------------------------------------------
# API + cache configuration
# ---------------------------------------------------------------------------

_BASE_URL = "https://api.open-meteo.com/v1/forecast"

REQUIRED_HOURLY: list[str] = [
    "et0_fao_evapotranspiration",
    "precipitation",
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "shortwave_radiation",
    "vapour_pressure_deficit",
]

_env_cache_dir = os.environ.get("OPEN_METEO_CACHE_DIR")
_DEFAULT_CACHE_DIR: Path = (
    Path(_env_cache_dir)
    if _env_cache_dir
    else Path.home() / ".cache" / "pollino" / "open_meteo"
)
_DEFAULT_CACHE_TTL: timedelta = timedelta(
    seconds=int(os.environ.get("OPEN_METEO_CACHE_TTL_SECONDS", "3600"))
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class OpenMeteoError(Exception):
    """HTTP-level failure from the Open-Meteo API."""


class SchemaDriftError(OpenMeteoError):
    """API response is missing a required field.

    A silent drop (e.g. vapour_pressure_deficit disappearing) would disable
    the iron-rule-3 independent heat-stress channel without any error.
    """


@dataclass
class HourlyForecast:
    """Parsed hourly forecast for the orchard site."""

    time: list[datetime]
    et0_fao_evapotranspiration: list[float]
    precipitation: list[float]
    temperature_2m: list[float]
    relative_humidity_2m: list[float]
    wind_speed_10m: list[float]
    shortwave_radiation: list[float]
    vapour_pressure_deficit: list[float]
    latitude: float
    longitude: float


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_response(payload: dict) -> HourlyForecast:
    """Validate and parse an Open-Meteo JSON payload.

    Raises SchemaDriftError immediately if any required field is absent so
    schema changes surface at ingestion rather than propagating as None/NaN.
    """
    hourly = payload.get("hourly", {})
    missing = [v for v in REQUIRED_HOURLY if v not in hourly]
    if missing:
        raise SchemaDriftError(
            f"Open-Meteo response missing required fields: {missing}"
        )
    return HourlyForecast(
        time=[datetime.fromisoformat(t) for t in hourly["time"]],
        et0_fao_evapotranspiration=hourly["et0_fao_evapotranspiration"],
        precipitation=hourly["precipitation"],
        temperature_2m=hourly["temperature_2m"],
        relative_humidity_2m=hourly["relative_humidity_2m"],
        wind_speed_10m=hourly["wind_speed_10m"],
        shortwave_radiation=hourly["shortwave_radiation"],
        vapour_pressure_deficit=hourly["vapour_pressure_deficit"],
        latitude=payload.get("latitude", float("nan")),
        longitude=payload.get("longitude", float("nan")),
    )


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_key(lat: float, lon: float, hours: int) -> str:
    return hashlib.sha256(f"{lat:.6f}:{lon:.6f}:{hours}".encode()).hexdigest()[:16]


def _cache_path(lat: float, lon: float, hours: int, cache_dir: Path) -> Path:
    return cache_dir / f"forecast_{_cache_key(lat, lon, hours)}.json"


def _load_cache(path: Path, ttl: timedelta) -> dict | None:
    """Return the cached payload if it exists and is fresh, else None."""
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text())
        age = datetime.now() - datetime.fromisoformat(entry["fetched_at"])
        if age > ttl:
            return None
        return entry["payload"]
    except (KeyError, ValueError, OSError):
        return None


def _save_cache(path: Path, payload: dict) -> None:
    """Atomically write payload to the cache (temp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({"fetched_at": datetime.now().isoformat(), "payload": payload})
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            os.write(fd, entry.encode())
        finally:
            os.close(fd)
        Path(tmp).replace(path)
    except OSError:
        pass  # cache write failures are non-fatal


# ---------------------------------------------------------------------------
# Public fetch function
# ---------------------------------------------------------------------------


def fetch_forecast(
    lat: float = ORCHARD_LAT,
    lon: float = ORCHARD_LON,
    hours: int = 48,
    *,
    client: httpx.Client | None = None,
    cache_dir: Path | None = None,
    cache_ttl: timedelta | None = None,
) -> HourlyForecast:
    """Fetch an hourly forecast, serving from the file cache when fresh.

    Parameters
    ----------
    lat, lon    : orchard coordinates (default: Piana di Cammarata).
    hours       : forecast horizon in hours (default 48).
    client      : injectable httpx.Client; a fresh client is used if omitted.
    cache_dir   : directory for cached responses; defaults to
                  OPEN_METEO_CACHE_DIR env var or ~/.cache/pollino/open_meteo/.
                  Pass a tmp_path in tests to isolate cache state.
    cache_ttl   : how long a cached response is considered fresh
                  (default 1 h from OPEN_METEO_CACHE_TTL_SECONDS).
                  Pass timedelta(0) to always re-fetch.

    Raises
    ------
    OpenMeteoError   on any HTTP error (4xx / 5xx).
    SchemaDriftError if the response omits a required variable.
    """
    _cache_dir = cache_dir if cache_dir is not None else _DEFAULT_CACHE_DIR
    _ttl = cache_ttl if cache_ttl is not None else _DEFAULT_CACHE_TTL
    path = _cache_path(lat, lon, hours, _cache_dir)

    cached = _load_cache(path, _ttl)
    if cached is not None:
        return parse_response(cached)

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(REQUIRED_HOURLY),
        "forecast_hours": hours,
        "wind_speed_unit": "ms",
        "timezone": "auto",
    }
    _client = client or httpx.Client()
    try:
        resp = _client.get(_BASE_URL, params=params, timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise OpenMeteoError(
            f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        ) from exc

    payload = resp.json()
    _save_cache(path, payload)
    return parse_response(payload)
