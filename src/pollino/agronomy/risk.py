"""
Irrigation risk traffic-light, forward projection, and hysteresis — pure functions, no I/O.

Risk levels:
  "G" (GREEN)  : Dr ≤ RAW — no intervention needed
  "Y" (YELLOW) : RAW < Dr < MAD — monitor; schedule irrigation
  "R" (RED)    : Dr ≥ MAD  OR  VPD ≥ 2 kPa (iron rule 3)

References:
  Allen et al. (1998) FAO-56, Eq.85 (daily depletion).
  Egea et al. (2017) — VPD stomatal closure threshold for hazelnut.
"""

from pollino.agronomy.stress import VPD_STRESS_THRESHOLD
from pollino.agronomy.water_balance import daily_depletion


def traffic_light(dr: float, raw: float, mad: float) -> str:
    """Stateless soil-based traffic light.

    G : Dr ≤ RAW
    Y : RAW < Dr < MAD
    R : Dr ≥ MAD
    """
    if dr <= raw:
        return "G"
    if dr < mad:
        return "Y"
    return "R"


def traffic_light_hysteresis(
    dr: float,
    raw: float,
    mad: float,
    prev_level: str,
    hysteresis_mm: float = 2.0,
) -> str:
    """Stateful traffic light with downward hysteresis to prevent boundary flapping.

    Upward transitions are immediate (react quickly to worsening stress).
    Downward transitions require Dr to fall below threshold − hysteresis_mm:
      R → Y : Dr < MAD − hysteresis_mm
      Y → G : Dr < RAW − hysteresis_mm

    Parameters
    ----------
    prev_level    : risk level at the previous time step ("G", "Y", or "R")
    hysteresis_mm : deadband below each threshold for downward transitions (mm)
    """
    # Upward: always immediate
    if dr >= mad:
        return "R"

    if prev_level == "R":
        # Downward R→Y only once clearly below MAD − band
        return "Y" if dr < (mad - hysteresis_mm) else "R"

    if dr > raw:
        return "Y"

    if prev_level == "Y":
        # Downward Y→G only once clearly below RAW − band
        return "G" if dr < (raw - hysteresis_mm) else "Y"

    return "G"


def project_dr(
    dr: float,
    forecast_etc: list[float],
    forecast_precip: list[float],
    taw: float,
) -> list[float]:
    """Apply FAO-56 Eq.85 forward for each forecast day.

    Returns a list of projected Dr values, one per forecast day.
    No runoff, irrigation, capillary rise, or deep percolation assumed in forecast.
    """
    results = []
    for etc_day, precip_day in zip(forecast_etc, forecast_precip):
        dr = daily_depletion(
            dr_prev=dr,
            precip=precip_day,
            runoff=0.0,
            irrigation=0.0,
            cr=0.0,
            etc_mm=etc_day,
            dp=0.0,
            taw_mm=taw,
        )
        results.append(dr)
    return results


def risk_lookahead(
    dr: float,
    raw: float,
    mad: float,
    taw: float,
    forecast_etc: list[float],
    forecast_precip: list[float],
    vpd: float,
) -> str:
    """Forecast-aware risk assessment combining soil depletion, lookahead, and VPD.

    Returns the worst risk level across:
      1. Current Dr vs thresholds (stateless traffic light).
      2. Projected Dr over the forecast horizon — issues RED now if Dr will
         cross MAD within 1–2 days, giving lead time for irrigation.
      3. VPD ≥ 2 kPa independent channel (iron rule 3; Egea et al. 2017).
    """
    _SEVERITY = {"G": 0, "Y": 1, "R": 2}

    worst = traffic_light(dr=dr, raw=raw, mad=mad)

    # VPD independent channel — fires regardless of soil state
    if vpd >= VPD_STRESS_THRESHOLD:
        worst = "R"

    if worst == "R":
        return "R"

    # Forward projection
    projected = project_dr(
        dr=dr,
        forecast_etc=forecast_etc,
        forecast_precip=forecast_precip,
        taw=taw,
    )
    for dr_proj in projected:
        level = traffic_light(dr=dr_proj, raw=raw, mad=mad)
        if _SEVERITY[level] > _SEVERITY[worst]:
            worst = level
        if worst == "R":
            return "R"

    return worst
