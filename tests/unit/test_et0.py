"""
FAO-56 oracle tests for pollino.agronomy.et0.

All oracle values are taken directly from:
  Allen et al. (1998) FAO Irrigation and Drainage Paper 56.
  Example 18 (Brussels, 6 July)  → ET0 ≈ 3.9 mm/day
  Example 17 (Bangkok, 16 May)   → ET0 ≈ 5.7 mm/day

These tests are written BEFORE the implementation (TDD).
They must fail with ImportError until et0.py is created.
"""

import pytest

from pollino.agronomy.et0 import (
    actual_vapour_pressure,
    atmospheric_pressure,
    et0_penman_monteith,
    mean_saturation_vapour_pressure,
    psychrometric_constant,
    saturation_vapour_pressure,
    slope_vapour_pressure_curve,
    wind_speed_2m,
)

# ---------------------------------------------------------------------------
# Example 18 inputs (Brussels, 6 July, FAO-56 p.72)
# ---------------------------------------------------------------------------
EX18 = dict(
    t_max=21.5,
    t_min=12.3,
    rh_max=84.0,
    rh_min=63.0,
    u2=2.078,  # converted from u10=10 km/h via wind_speed_2m()
    rn=13.28,  # MJ m⁻² d⁻¹, net radiation (given in example)
    z=100.0,  # m elevation
    g=0.0,  # daily G ≈ 0
)

# Example 17 inputs (Bangkok, 16 May, FAO-56 p.69)
# Rn=14.45 derived from Ra=38.06, n=8.5h, N=12.26h (radiation chain in book)
EX17 = dict(
    t_max=34.8,
    t_min=25.6,
    rh_max=90.0,
    rh_min=52.0,
    u2=2.0,
    rn=14.45,  # MJ m⁻² d⁻¹
    z=2.0,
    g=0.0,
)


# ---------------------------------------------------------------------------
# Intermediate helpers — Example 18 (FAO-56 Table in Example 18)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "t, expected",
    [
        (21.5, 2.563),  # e°(Tmax), FAO-56 Ex.18
        (12.3, 1.431),  # e°(Tmin), FAO-56 Ex.18
    ],
)
def test_saturation_vapour_pressure(t, expected):
    """e°(T) = 0.6108·exp(17.27T/(T+237.3))  (FAO-56 Eq.11)"""
    assert saturation_vapour_pressure(t) == pytest.approx(expected, abs=0.002)


def test_mean_saturation_vapour_pressure_ex18():
    """es = (e°(Tmax)+e°(Tmin))/2 = 1.997 kPa  (FAO-56 Eq.12, Ex.18)"""
    assert mean_saturation_vapour_pressure(21.5, 12.3) == pytest.approx(
        1.997, abs=0.002
    )


def test_actual_vapour_pressure_ex18():
    """ea = (e°(Tmin)·RHmax + e°(Tmax)·RHmin) / 200 = 1.409 kPa  (FAO-56 Eq.17, Ex.18)"""
    assert actual_vapour_pressure(12.3, 21.5, 84.0, 63.0) == pytest.approx(
        1.409, abs=0.002
    )


def test_slope_vapour_pressure_curve_ex18():
    """Δ = 4098·e°(Tmean)/(Tmean+237.3)² = 0.122 kPa/°C  (FAO-56 Eq.13, Ex.18)"""
    t_mean = (21.5 + 12.3) / 2  # 16.9 °C
    assert slope_vapour_pressure_curve(t_mean) == pytest.approx(0.122, abs=0.001)


def test_atmospheric_pressure_ex18():
    """P = 101.3·((293−0.0065z)/293)^5.26 = 100.1 kPa  (FAO-56 Eq.7, Ex.18)"""
    assert atmospheric_pressure(100.0) == pytest.approx(100.1, abs=0.1)


def test_psychrometric_constant_ex18():
    """γ = 0.000665·P = 0.0666 kPa/°C  (FAO-56 Eq.8, Ex.18)"""
    assert psychrometric_constant(100.0) == pytest.approx(0.0666, abs=0.001)


def test_wind_speed_2m_ex18():
    """u2 = u10·4.87/ln(67.8·10−5.42) = 2.078 m/s  (FAO-56 Eq.47, Ex.18)

    u10 = 10 km/h = 2.778 m/s, measured at z_wind=10 m.
    """
    u10_ms = 10.0 / 3.6  # 10 km/h → m/s
    assert wind_speed_2m(u10_ms, z_wind=10.0) == pytest.approx(2.078, abs=0.002)


# ---------------------------------------------------------------------------
# Full ET0 oracles
# ---------------------------------------------------------------------------


def test_et0_example18_brussels():
    """ET0 ≈ 3.9 mm/day — FAO-56 Example 18 (Brussels, 6 July)."""
    result = et0_penman_monteith(**EX18)
    assert result == pytest.approx(3.9, abs=0.05)


def test_et0_example17_bangkok():
    """ET0 ≈ 5.7 mm/day — FAO-56 Example 17 (Bangkok, 16 May)."""
    result = et0_penman_monteith(**EX17)
    assert result == pytest.approx(5.7, abs=0.05)
