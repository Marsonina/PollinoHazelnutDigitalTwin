"""
Tests for pollino.agronomy.risk — traffic-light, lookahead, hysteresis.

Risk levels:
  G (GREEN)  : Dr ≤ RAW — no intervention needed
  Y (YELLOW) : RAW < Dr < MAD — monitor; irrigate soon
  R (RED)    : Dr ≥ MAD  OR  VPD ≥ 2 kPa (independent channel, iron rule 3)

Hysteresis prevents flapping when Dr oscillates around a boundary:
  upward  transitions are immediate (worsen quickly)
  downward transitions require Dr to fall below threshold − band

Forward lookahead: RED is issued NOW if Dr will cross MAD within 1–2 forecast days,
even if the current Dr is still in YELLOW.

These tests are written BEFORE the implementation (TDD).
They must fail with ImportError until risk.py is created.
"""

import pytest

from pollino.agronomy.risk import (
    project_dr,
    risk_lookahead,
    traffic_light,
    traffic_light_hysteresis,
)

# ---------------------------------------------------------------------------
# Shared parameters — alluvial loam, hazelnut scheduling defaults
# ---------------------------------------------------------------------------
TAW = 120.0  # mm
RAW = 60.0  # mm  (p=0.50 · TAW)
MAD = 90.0  # mm  management allowed deficit, Y/R boundary
BAND = 3.0  # mm  hysteresis band


# ---------------------------------------------------------------------------
# traffic_light — stateless soil-based thresholds
# G: Dr ≤ RAW   Y: RAW < Dr < MAD   R: Dr ≥ MAD
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dr, expected",
    [
        (0.0, "G"),  # field capacity
        (30.0, "G"),  # well within green zone
        (60.0, "G"),  # Dr = RAW exactly — boundary is inclusive GREEN
        (60.001, "Y"),  # just over RAW — enters YELLOW
        (89.9, "Y"),  # just under MAD
        (90.0, "R"),  # Dr = MAD — boundary is inclusive RED
        (91.0, "R"),  # clearly red
        (120.0, "R"),  # at TAW — maximum depletion
    ],
)
def test_traffic_light_boundaries(dr, expected):
    """G ≤ RAW < Y < MAD ≤ R  (stateless thresholds)."""
    assert traffic_light(dr=dr, raw=RAW, mad=MAD) == expected


# ---------------------------------------------------------------------------
# project_dr — forward simulation of Eq.85 over forecast horizon
# ---------------------------------------------------------------------------


def test_project_dr_single_day():
    """One forecast day dries the soil by ETc with no precipitation.  (FAO-56 Eq.85)"""
    result = project_dr(dr=50.0, forecast_etc=[6.65], forecast_precip=[0.0], taw=TAW)
    assert result == pytest.approx([56.65], abs=0.01)


def test_project_dr_two_days_heatwave():
    """Two-day heatwave: Dr=80 + 6 mm/day reaches 92 mm on day 2.  (FAO-56 Eq.85)"""
    result = project_dr(
        dr=80.0, forecast_etc=[6.0, 6.0], forecast_precip=[0.0, 0.0], taw=TAW
    )
    assert result == pytest.approx([86.0, 92.0], abs=0.01)


def test_project_dr_rain_slows_depletion():
    """Forecasted rain reduces net depletion."""
    # dr=80, ETc=6, precip=4 each day → net +2/day
    result = project_dr(
        dr=80.0, forecast_etc=[6.0, 6.0], forecast_precip=[4.0, 4.0], taw=TAW
    )
    assert result == pytest.approx([82.0, 84.0], abs=0.01)


def test_project_dr_clamped_at_taw():
    """Projected Dr cannot exceed TAW.  (FAO-56 Eq.85 clamp)"""
    result = project_dr(dr=118.0, forecast_etc=[10.0], forecast_precip=[0.0], taw=TAW)
    assert result[0] == pytest.approx(TAW, abs=0.01)


def test_project_dr_clamped_at_zero():
    """Heavy rain cannot drive Dr below 0.  (FAO-56 Eq.85 clamp)"""
    result = project_dr(dr=5.0, forecast_etc=[1.0], forecast_precip=[50.0], taw=TAW)
    assert result[0] == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# risk_lookahead — forecast-aware assessment: soil + VPD + horizon
# ---------------------------------------------------------------------------


def test_lookahead_current_yellow_projects_to_red():
    """Current Dr=80 is YELLOW, but day-2 Dr=92 > MAD=90 → issue RED now."""
    level = risk_lookahead(
        dr=80.0,
        raw=RAW,
        mad=MAD,
        taw=TAW,
        forecast_etc=[6.0, 6.0],
        forecast_precip=[0.0, 0.0],
        vpd=0.0,
    )
    assert level == "R"


def test_lookahead_green_stays_green_no_threat():
    """Single hot well-watered day: Dr=0, ETc=6.7, VPD=1.5 kPa → no false positive."""
    level = risk_lookahead(
        dr=0.0,
        raw=RAW,
        mad=MAD,
        taw=TAW,
        forecast_etc=[6.7],
        forecast_precip=[0.0],
        vpd=1.5,
    )
    assert level == "G"


def test_lookahead_vpd_triggers_red_independent_of_soil():
    """VPD ≥ 2 kPa fires RED even when Dr=0 (soil at field capacity).  Iron rule 3."""
    level = risk_lookahead(
        dr=0.0,
        raw=RAW,
        mad=MAD,
        taw=TAW,
        forecast_etc=[0.0],
        forecast_precip=[0.0],
        vpd=3.0,
    )
    assert level == "R"


def test_lookahead_compound_vpd_zero_rain_dr_above_raw():
    """Compound: Dr > RAW + VPD ≥ 2 kPa + zero rain forecast → RED.  Iron rule 3."""
    level = risk_lookahead(
        dr=65.0,
        raw=RAW,
        mad=MAD,
        taw=TAW,  # Dr > RAW → normally YELLOW
        forecast_etc=[5.0, 5.0],
        forecast_precip=[0.0, 0.0],
        vpd=2.5,
    )
    assert level == "R"


def test_lookahead_yellow_with_rain_incoming_not_red():
    """Dr in YELLOW, VPD high, but significant rain forecast → not elevated to RED."""
    level = risk_lookahead(
        dr=65.0,
        raw=RAW,
        mad=MAD,
        taw=TAW,
        forecast_etc=[5.0, 5.0],
        forecast_precip=[20.0, 0.0],  # 20 mm tomorrow
        vpd=0.0,
    )
    # 65 - 20 + 5 = 50 (day1, back to G); 50 + 5 = 55 (day2, still G)
    assert level == "Y"


def test_lookahead_currently_red_stays_red():
    """Dr already ≥ MAD → RED regardless of forecast."""
    level = risk_lookahead(
        dr=MAD,
        raw=RAW,
        mad=MAD,
        taw=TAW,
        forecast_etc=[2.0],
        forecast_precip=[10.0],
        vpd=0.0,
    )
    assert level == "R"


# ---------------------------------------------------------------------------
# traffic_light_hysteresis — stateful, prevents boundary flapping
#
# Upward transitions are IMMEDIATE (react fast to stress).
# Downward transitions require Dr to fall below threshold − band.
#   Y→G : Dr < RAW − band
#   R→Y : Dr < MAD − band
# ---------------------------------------------------------------------------


def test_hysteresis_green_to_yellow_immediate():
    """Crossing RAW upward gives YELLOW immediately — no delayed reaction."""
    assert (
        traffic_light_hysteresis(
            dr=61.0, raw=RAW, mad=MAD, prev_level="G", hysteresis_mm=BAND
        )
        == "Y"
    )


def test_hysteresis_yellow_does_not_flip_to_green_within_band():
    """Dr drops below RAW but stays within hysteresis band — holds YELLOW."""
    # RAW - BAND = 57; Dr=59 is between 57 and 60 → stays Y
    assert (
        traffic_light_hysteresis(
            dr=59.0, raw=RAW, mad=MAD, prev_level="Y", hysteresis_mm=BAND
        )
        == "Y"
    )


def test_hysteresis_yellow_returns_to_green_below_band():
    """Dr drops clearly below RAW − band → allowed to return to GREEN."""
    # RAW - BAND = 57; Dr=56 < 57 → G
    assert (
        traffic_light_hysteresis(
            dr=56.0, raw=RAW, mad=MAD, prev_level="Y", hysteresis_mm=BAND
        )
        == "G"
    )


def test_hysteresis_yellow_to_red_immediate():
    """Crossing MAD upward gives RED immediately."""
    assert (
        traffic_light_hysteresis(
            dr=91.0, raw=RAW, mad=MAD, prev_level="Y", hysteresis_mm=BAND
        )
        == "R"
    )


def test_hysteresis_red_does_not_flip_within_band():
    """Dr drops below MAD but stays within hysteresis band — holds RED."""
    # MAD - BAND = 87; Dr=89 is between 87 and 90 → stays R
    assert (
        traffic_light_hysteresis(
            dr=89.0, raw=RAW, mad=MAD, prev_level="R", hysteresis_mm=BAND
        )
        == "R"
    )


def test_hysteresis_red_returns_to_yellow_below_band():
    """Dr drops clearly below MAD − band → allowed to return to YELLOW."""
    # MAD - BAND = 87; Dr=86 < 87 → Y
    assert (
        traffic_light_hysteresis(
            dr=86.0, raw=RAW, mad=MAD, prev_level="R", hysteresis_mm=BAND
        )
        == "Y"
    )


def test_hysteresis_no_flap_oscillation():
    """Dr oscillating ±1 mm around RAW must NOT alternate G/Y/G/Y (flapping)."""
    prev = "G"
    states = []
    for dr_val in [61.0, 59.0, 61.0, 59.0, 61.0, 59.0]:  # crosses RAW each step
        level = traffic_light_hysteresis(
            dr=dr_val, raw=RAW, mad=MAD, prev_level=prev, hysteresis_mm=BAND
        )
        states.append(level)
        prev = level

    # First crossing → Y; subsequent oscillations must stay Y (no flapping)
    assert states[0] == "Y"
    assert all(s == "Y" for s in states), f"Flapping detected: {states}"
