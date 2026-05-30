"""Wire Open-Meteo hourly forecast → daily aggregates → risk lookahead.

Data flow:
  HourlyForecast (48 h)
      └─ daily_etc_and_precip()   aggregate hourly ET0/precip → per-day lists
      └─ max VPD over horizon      worst-case independent heat-stress signal
      └─ risk_lookahead()          G / Y / R from agronomy core

VPD treatment: the full forecast horizon is scanned for its peak value.
Iron rule 3 — "high *forecast* VPD is an INDEPENDENT red-risk channel" —
means any hour ≥ 2 kPa anywhere in the 48-h window should trigger RED now,
not only when VPD is already high at query time.
"""

from __future__ import annotations

from pollino.agronomy.risk import risk_lookahead
from pollino.ingest.weather.open_meteo import HourlyForecast


def daily_etc_and_precip(
    forecast: HourlyForecast,
    kc: float,
    days: int = 2,
) -> tuple[list[float], list[float]]:
    """Aggregate hourly forecast arrays into per-day ETc and precipitation.

    Parameters
    ----------
    forecast : parsed Open-Meteo hourly response.
    kc       : crop coefficient (Kc) for the current phenological phase.
    days     : number of forecast days to aggregate (each spans 24 h).

    Returns
    -------
    (forecast_etc, forecast_precip) — two lists of length *days*.
    ETc = Kc × daily ET0 sum  (mm/day, single-coefficient approach, FAO-56 Eq.58).
    """
    etc, precip = [], []
    for d in range(days):
        start = d * 24
        end = start + 24
        et0_daily = sum(forecast.et0_fao_evapotranspiration[start:end])
        etc.append(kc * et0_daily)
        precip.append(sum(forecast.precipitation[start:end]))
    return etc, precip


def assess(
    forecast: HourlyForecast,
    *,
    dr: float,
    raw: float,
    mad: float,
    taw: float,
    kc: float,
    days: int = 2,
) -> str:
    """Compute the irrigation risk level from a 48-h hourly forecast.

    Parameters
    ----------
    forecast : parsed Open-Meteo HourlyForecast (typically 48 h).
    dr       : current root-zone depletion in mm (from latest sensor reading
               or yesterday's balance).
    raw      : readily available water threshold (mm) — G/Y boundary.
    mad      : management allowed deficit (mm) — Y/R soil-stress boundary.
    taw      : total available water (mm) — upper clamp.
    kc       : crop coefficient for the current phenological phase.
    days     : forecast horizon in days (default 2 = 48 h).

    Returns
    -------
    "G", "Y", or "R" — the worst-case risk over the forecast window,
    combining soil-depletion lookahead and the VPD independent channel.
    """
    forecast_etc, forecast_precip = daily_etc_and_precip(forecast, kc, days)
    vpd_max = max(forecast.vapour_pressure_deficit)

    return risk_lookahead(
        dr=dr,
        raw=raw,
        mad=mad,
        taw=taw,
        forecast_etc=forecast_etc,
        forecast_precip=forecast_precip,
        vpd=vpd_max,
    )
