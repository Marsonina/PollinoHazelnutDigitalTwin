"""
FAO-56 oracle tests for pollino.agronomy.stress.

Equations:
  Ks  — FAO-56 Eq.84  (soil water stress coefficient)
  ETc_adj — FAO-56 Eq.80  (stress-adjusted crop ET)
  VPD stress — INDEPENDENT channel (iron rule 3); hazelnut stomatal closure ≥ 2 kPa.
    Source: project iron rule + hazelnut physiology (Egea et al. 2017).

These tests are written BEFORE the implementation (TDD).
They must fail with ImportError until stress.py is created.
"""

import pytest

from pollino.agronomy.stress import (
    etc_adj,
    ks,
    vpd_stress,
)

# ---------------------------------------------------------------------------
# Shared parameters — alluvial loam, hazelnut scheduling defaults
# ---------------------------------------------------------------------------
TAW = 120.0  # mm  (θFC=0.30, θWP=0.15, Zr=0.8 m)
P = 0.50  # depletion fraction (walnut analogue, FAO-56)
RAW = P * TAW  # 60 mm


# ---------------------------------------------------------------------------
# Ks — soil water stress coefficient  (FAO-56 Eq.84)
#
# Dr ≤ RAW  →  Ks = 1  (no stress)
# Dr > RAW  →  Ks = (TAW − Dr) / ((1 − p)·TAW)
# Clamp output to [0, 1]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dr, expected",
    [
        (0.0, 1.0),  # field capacity — no stress
        (30.0, 1.0),  # Dr < RAW — no stress
        (60.0, 1.0),  # Dr = RAW exactly — still Ks=1 (boundary, FAO-56 Eq.84)
        (90.0, 0.5),  # (120−90) / (0.5·120) = 30/60
        (110.0, 1.0 / 6.0),  # (120−110) / (0.5·120) = 10/60
        (120.0, 0.0),  # Dr = TAW → Ks = 0
        (150.0, 0.0),  # Dr > TAW → clamp to 0
    ],
)
def test_ks_values(dr, expected):
    """Ks = 1 below RAW; linear decline above; clamp at 0.  FAO-56 Eq.84."""
    assert ks(dr=dr, taw=TAW, p=P) == pytest.approx(expected, abs=1e-6)


def test_ks_continuous_just_above_raw():
    """Ks is strictly < 1 immediately above RAW — the boundary is Dr ≤ RAW, not <."""
    assert ks(dr=RAW + 0.001, taw=TAW, p=P) < 1.0


def test_ks_monotone_decreasing_above_raw():
    """Ks decreases monotonically as Dr increases above RAW.  FAO-56 Eq.84."""
    dr_values = [RAW + i * 5 for i in range(1, 13)]  # RAW+5 … RAW+60
    ks_values = [ks(dr=dr, taw=TAW, p=P) for dr in dr_values]
    for a, b in zip(ks_values, ks_values[1:]):
        assert a >= b


def test_ks_clamp_below_zero_is_impossible():
    """Ks must never return a negative value, even for Dr >> TAW."""
    assert ks(dr=1000.0, taw=TAW, p=P) == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Divide-by-zero guard — (1−p)·TAW in denominator
# ---------------------------------------------------------------------------


def test_ks_raises_on_zero_taw():
    """TAW=0 is physically impossible; stress.py must raise ValueError."""
    with pytest.raises(ValueError):
        ks(dr=0.0, taw=0.0, p=P)


def test_ks_raises_on_negative_taw():
    """Negative TAW (θFC ≤ θWP) must be caught here as a second line of defence."""
    with pytest.raises(ValueError):
        ks(dr=0.0, taw=-10.0, p=P)


def test_ks_guard_p_equals_one():
    """p=1 → denominator (1−p)·TAW = 0; must raise ValueError (explicit guard)."""
    with pytest.raises(ValueError):
        ks(dr=TAW - 1, taw=TAW, p=1.0)


# ---------------------------------------------------------------------------
# VPD stress — INDEPENDENT channel  (iron rule 3)
#
# Hazelnut closes stomata at high VPD even with full soil water.
# vpd_stress() must never be influenced by Dr or Ks.
# Threshold: ≥ 2 kPa  (skill file; Egea et al. 2017 physiology)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vpd, expected",
    [
        (0.0, False),
        (1.5, False),
        (1.99, False),
        (2.0, True),  # threshold is inclusive
        (2.5, True),
        (4.0, True),
    ],
)
def test_vpd_stress_threshold(vpd, expected):
    """vpd_stress flags True at VPD ≥ 2 kPa.  Iron rule 3; Egea et al. (2017)."""
    assert vpd_stress(vpd=vpd) is expected


def test_vpd_stress_rejects_nan():
    """NaN VPD must raise ValueError — fail-open on the heat-stress channel is unsafe."""
    import math

    with pytest.raises(ValueError):
        vpd_stress(vpd=float("nan"))

    assert math.isnan(float("nan"))  # sanity — confirms nan propagation risk


def test_vpd_stress_fires_with_full_soil_water():
    """VPD stress is INDEPENDENT: fires when Ks=1 (Dr=0, no soil stress)."""
    assert ks(dr=0.0, taw=TAW, p=P) == pytest.approx(1.0)  # soil fine
    assert vpd_stress(vpd=3.0) is True  # VPD fires anyway


def test_vpd_stress_silent_under_soil_stress():
    """VPD stress is INDEPENDENT: silent at low VPD even when soil Ks < 1."""
    assert ks(dr=90.0, taw=TAW, p=P) == pytest.approx(0.5)  # soil stressed
    assert vpd_stress(vpd=1.5) is False  # VPD silent


# ---------------------------------------------------------------------------
# ETc_adj — stress-adjusted crop evapotranspiration  (FAO-56 Eq.80)
#
# ETc_adj = Ks · Kc · ET0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ks_val, kc, et0, expected",
    [
        (1.0, 0.95, 5.50, 5.225),  # no stress — same as ETc
        (0.5, 0.95, 6.00, 2.850),  # mid-stress, heatwave ET0
        (0.0, 0.95, 6.70, 0.000),  # full depletion — ETc_adj = 0
        (1 / 6, 0.95, 6.00, 0.950),  # deep stress (Dr=110 case above)
    ],
)
def test_etc_adj(ks_val, kc, et0, expected):
    """ETc_adj = Ks·Kc·ET0  (FAO-56 Eq.80)."""
    assert etc_adj(ks=ks_val, kc=kc, et0=et0) == pytest.approx(expected, abs=0.001)
