"""
FAO-56 oracle tests for pollino.agronomy.water_balance.

All equations reference Allen et al. (1998) FAO Irrigation and Drainage Paper 56.
Hazelnut Kc mid-season range 0.9–1.04 from Egea et al. (2017); project default 0.95.

These tests are written BEFORE the implementation (TDD).
They must fail with ImportError until water_balance.py is created.
"""

import pytest

from pollino.agronomy.water_balance import (
    KC_HAZELNUT_MID,
    daily_depletion,
    etc,
    kc_adjust,
    p_adjust,
    raw,
    taw,
)

# ---------------------------------------------------------------------------
# TAW — total available water  (FAO-56: TAW = 1000·(θFC−θWP)·Zr)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "theta_fc, theta_wp, zr, expected",
    [
        (0.30, 0.15, 0.80, 120.0),  # typical alluvial loam, Zr=0.8 m
        (0.28, 0.12, 1.00, 160.0),  # lower-bound θFC, full Zr
        (0.32, 0.18, 0.60, 84.0),  # upper-bound θFC, shallow Zr
    ],
)
def test_taw(theta_fc, theta_wp, zr, expected):
    """TAW = 1000·(θFC−θWP)·Zr  (FAO-56 Eq.82; alluvial loam bounds from skill)."""
    assert taw(theta_fc, theta_wp, zr) == pytest.approx(expected, abs=0.1)


# ---------------------------------------------------------------------------
# RAW — readily available water  (FAO-56: RAW = p·TAW)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "taw_mm, p, expected",
    [
        (120.0, 0.50, 60.0),
        (160.0, 0.50, 80.0),
        (120.0, 0.45, 54.0),  # p adjusted for high ETc
    ],
)
def test_raw(taw_mm, p, expected):
    """RAW = p·TAW  (FAO-56 Eq.83)."""
    assert raw(taw_mm, p) == pytest.approx(expected, abs=0.1)


# ---------------------------------------------------------------------------
# p_adjust — FAO-56 climate/ETc adjustment for depletion fraction
# (FAO-56 Table 22 footnote: p = p_tab + 0.04·(5−ETc), bounded [0.1, 0.8])
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "p_std, etc_mm, expected",
    [
        (0.50, 5.0, 0.50),  # neutral: ETc=5 → no change
        (0.50, 6.0, 0.46),  # high ETc → lower p (stress sooner)
        (0.50, 3.0, 0.58),  # low ETc  → higher p (stress later)
        (0.50, 9.0, 0.34),  # extreme high ETc, still above 0.1 floor
    ],
)
def test_p_adjust(p_std, etc_mm, expected):
    """p = p_std + 0.04·(5−ETc), clamped [0.1, 0.8]  (FAO-56 Table 22 note)."""
    assert p_adjust(p_std, etc_mm) == pytest.approx(expected, abs=0.01)


def test_p_adjust_lower_bound():
    """p_adjust must not go below 0.1."""
    assert p_adjust(0.10, 99.0) >= 0.10


def test_p_adjust_upper_bound():
    """p_adjust must not exceed 0.8."""
    assert p_adjust(0.80, 0.0) <= 0.80


# ---------------------------------------------------------------------------
# ETc — crop evapotranspiration  (FAO-56 Eq.58: ETc = Kc·ET0)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kc, et0, expected",
    [
        (0.95, 5.50, 5.225),
        (0.90, 3.90, 3.510),  # FAO-56 Ex.18 ET0 with lower Kc
        (1.04, 6.70, 6.968),  # upper Kc bound, heatwave ET0
    ],
)
def test_etc(kc, et0, expected):
    """ETc = Kc·ET0  (FAO-56 Eq.58; single-coefficient approach)."""
    assert etc(kc, et0) == pytest.approx(expected, abs=0.001)


# ---------------------------------------------------------------------------
# daily_depletion — Eq.85 Dr recursion with 0≤Dr≤TAW clamping
# ---------------------------------------------------------------------------


def test_daily_depletion_pure_drying():
    """No inputs: Dr grows by exactly ETc each day.  (FAO-56 Eq.85)"""
    # Dr,i = Dr,i−1 + ETc  when P=RO=I=CR=DP=0
    assert daily_depletion(
        dr_prev=40.0,
        precip=0,
        runoff=0,
        irrigation=0,
        cr=0,
        etc_mm=5.0,
        dp=0,
        taw_mm=120.0,
    ) == pytest.approx(45.0, abs=0.01)


def test_daily_depletion_rain_event():
    """Rain reduces Dr; runoff is subtracted from effective precip.  (FAO-56 Eq.85)"""
    # Dr,i = 60 − (20−5) − 0 − 0 + 4 + 0 = 49
    assert daily_depletion(
        dr_prev=60.0,
        precip=20.0,
        runoff=5.0,
        irrigation=0,
        cr=0,
        etc_mm=4.0,
        dp=0,
        taw_mm=120.0,
    ) == pytest.approx(49.0, abs=0.01)


def test_daily_depletion_irrigation():
    """Irrigation refills the deficit.  (FAO-56 Eq.85)"""
    # Dr,i = 80 − 0 − 30 − 0 + 6 + 0 = 56
    assert daily_depletion(
        dr_prev=80.0,
        precip=0,
        runoff=0,
        irrigation=30.0,
        cr=0,
        etc_mm=6.0,
        dp=0,
        taw_mm=120.0,
    ) == pytest.approx(56.0, abs=0.01)


def test_daily_depletion_clamp_upper():
    """Dr must not exceed TAW — soil cannot hold more deficit.  (FAO-56 Eq.85)"""
    # Unclamped: 115 + 10 = 125 > TAW=120 → clamp to 120
    result = daily_depletion(
        dr_prev=115.0,
        precip=0,
        runoff=0,
        irrigation=0,
        cr=0,
        etc_mm=10.0,
        dp=0,
        taw_mm=120.0,
    )
    assert result == pytest.approx(120.0, abs=0.01)


def test_daily_depletion_clamp_lower():
    """Dr must not go below 0 — excess water leaves as deep percolation.  (FAO-56 Eq.85)"""
    # Unclamped: 10 − 50 + 4 = −36 → clamp to 0
    result = daily_depletion(
        dr_prev=10.0,
        precip=50.0,
        runoff=0,
        irrigation=0,
        cr=0,
        etc_mm=4.0,
        dp=0,
        taw_mm=120.0,
    )
    assert result == pytest.approx(0.0, abs=0.01)


def test_daily_depletion_seven_day_recursion():
    """Seven days of drying from field capacity, then a rain event.  (FAO-56 Eq.85)"""
    dr = 0.0  # start at field capacity
    for _ in range(7):
        dr = daily_depletion(
            dr_prev=dr,
            precip=0,
            runoff=0,
            irrigation=0,
            cr=0,
            etc_mm=5.0,
            dp=0,
            taw_mm=120.0,
        )
    assert dr == pytest.approx(35.0, abs=0.01)  # 7 × 5 mm

    # Rain of 30 mm on day 8
    dr = daily_depletion(
        dr_prev=dr,
        precip=30.0,
        runoff=0,
        irrigation=0,
        cr=0,
        etc_mm=5.0,
        dp=0,
        taw_mm=120.0,
    )
    assert dr == pytest.approx(10.0, abs=0.01)  # 35 − 30 + 5


# ---------------------------------------------------------------------------
# kc_adjust — climate-adjusted Kc  (FAO-56 Eq.62 / skill formula)
# Kc_adj = Kc + [0.04·(u2−2) − 0.004·(RHmin−45)]·(h/3)^0.3
# ---------------------------------------------------------------------------


def test_kc_adjust_neutral_climate():
    """No adjustment at standard climate (u2=2, RHmin=45).  (FAO-56 Eq.62)"""
    # 0.95 + [0.04*(2-2) - 0.004*(45-45)] * (3/3)^0.3 = 0.95
    assert kc_adjust(0.95, u2=2.0, rh_min=45.0, h=3.0) == pytest.approx(0.95, abs=0.001)


def test_kc_adjust_hot_dry():
    """Higher u2 and lower RHmin both increase Kc_adj.  (FAO-56 Eq.62)"""
    # 0.95 + [0.04*(3-2) − 0.004*(30-45)]·(3/3)^0.3
    # = 0.95 + [0.04 + 0.06]·1.0 = 0.95 + 0.10 = 1.05
    assert kc_adjust(0.95, u2=3.0, rh_min=30.0, h=3.0) == pytest.approx(1.05, abs=0.005)


def test_kc_adjust_tall_canopy():
    """Taller canopy (h>3) amplifies the climate adjustment.  (FAO-56 Eq.62)"""
    # 0.95 + [0.04*(3-2) − 0.004*(30-45)]·(4/3)^0.3
    # (4/3)^0.3 ≈ 1.091
    # = 0.95 + 0.10 * 1.091 = 0.95 + 0.1091 ≈ 1.059
    assert kc_adjust(0.95, u2=3.0, rh_min=30.0, h=4.0) == pytest.approx(
        1.059, abs=0.005
    )


# ---------------------------------------------------------------------------
# Hazelnut Kc mid-season constant
# Source: Egea et al. (2017); project default 0.95 (drip, mature, Piana di Cammarata)
# ---------------------------------------------------------------------------


def test_kc_hazelnut_mid_within_cited_range():
    """Mid-season Kc must be within literature range 0.9–1.04  (Egea et al. 2017)."""
    assert 0.90 <= KC_HAZELNUT_MID <= 1.04


def test_kc_hazelnut_mid_project_default():
    """Project default mid-season Kc = 0.95 (drip, mature, Piana di Cammarata)."""
    assert KC_HAZELNUT_MID == pytest.approx(0.95, abs=1e-9)
