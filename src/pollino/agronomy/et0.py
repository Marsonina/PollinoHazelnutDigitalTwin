"""
FAO-56 Penman-Monteith reference evapotranspiration — pure functions, no I/O.

All equations reference Allen et al. (1998) FAO Irrigation and Drainage Paper 56.
"""

import math


def saturation_vapour_pressure(t: float) -> float:
    """e°(T) in kPa.  FAO-56 Eq.11."""
    return 0.6108 * math.exp(17.27 * t / (t + 237.3))


def mean_saturation_vapour_pressure(t_max: float, t_min: float) -> float:
    """es = (e°(Tmax) + e°(Tmin)) / 2  in kPa.  FAO-56 Eq.12."""
    return (saturation_vapour_pressure(t_max) + saturation_vapour_pressure(t_min)) / 2.0


def actual_vapour_pressure(
    t_min: float, t_max: float, rh_max: float, rh_min: float
) -> float:
    """ea in kPa from RHmax/RHmin.  FAO-56 Eq.17."""
    return (
        saturation_vapour_pressure(t_min) * rh_max / 100.0
        + saturation_vapour_pressure(t_max) * rh_min / 100.0
    ) / 2.0


def slope_vapour_pressure_curve(t_mean: float) -> float:
    """Δ in kPa/°C.  FAO-56 Eq.13."""
    return 4098.0 * saturation_vapour_pressure(t_mean) / (t_mean + 237.3) ** 2


def atmospheric_pressure(z: float) -> float:
    """P in kPa from elevation z (m).  FAO-56 Eq.7."""
    return 101.3 * ((293.0 - 0.0065 * z) / 293.0) ** 5.26


def psychrometric_constant(z: float) -> float:
    """γ in kPa/°C.  FAO-56 Eq.8."""
    return 0.000665 * atmospheric_pressure(z)


def wind_speed_2m(u_z: float, z_wind: float = 10.0) -> float:
    """Adjust wind speed measured at z_wind (m) to 2 m height.  FAO-56 Eq.47."""
    return u_z * 4.87 / math.log(67.8 * z_wind - 5.42)


def et0_penman_monteith(
    t_max: float,
    t_min: float,
    rh_max: float,
    rh_min: float,
    u2: float,
    rn: float,
    z: float,
    g: float = 0.0,
) -> float:
    """Daily ET0 (mm/day) via FAO-56 Penman-Monteith (Eq.6).

    Parameters
    ----------
    t_max, t_min : °C
    rh_max, rh_min : %
    u2 : wind speed at 2 m (m/s)
    rn : net radiation (MJ m⁻² d⁻¹)
    z : elevation (m)
    g : soil heat flux density (MJ m⁻² d⁻¹), ≈ 0 for daily step
    """
    t_mean = (t_max + t_min) / 2.0
    delta = slope_vapour_pressure_curve(t_mean)
    gamma = psychrometric_constant(z)
    es = mean_saturation_vapour_pressure(t_max, t_min)
    ea = actual_vapour_pressure(t_min, t_max, rh_max, rh_min)
    vpd = es - ea

    numerator = 0.408 * delta * (rn - g) + gamma * (900.0 / (t_mean + 273.0)) * u2 * vpd
    denominator = delta + gamma * (1.0 + 0.34 * u2)
    return numerator / denominator
