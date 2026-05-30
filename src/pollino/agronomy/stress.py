"""
FAO-56 soil water stress and VPD stress — pure functions, no I/O.

All equations reference Allen et al. (1998) FAO Irrigation and Drainage Paper 56.
VPD threshold: hazelnut stomatal closure ≥ 2 kPa (Egea et al. 2017; iron rule 3).
"""

# VPD threshold (kPa) above which hazelnut stomata close independently of soil water.
# Source: Egea et al. (2017); project iron rule 3.
import math

VPD_STRESS_THRESHOLD: float = 2.0


def ks(dr: float, taw: float, p: float) -> float:
    """Soil water stress coefficient.  FAO-56 Eq.84.

    Dr ≤ RAW  →  Ks = 1  (no stress)
    Dr > RAW  →  Ks = (TAW − Dr) / ((1 − p)·TAW)
    Output clamped to [0, 1].

    Parameters
    ----------
    dr  : root-zone depletion at end of day (mm); caller must clamp to [0, TAW]
    taw : total available water (mm); must be > 0
    p   : depletion fraction [0, 1); RAW = p·TAW
    """
    if taw <= 0:
        raise ValueError(f"TAW must be positive, got {taw}")
    if not (0.0 <= p < 1.0):
        raise ValueError(f"p must be in [0, 1), got {p}")

    raw = p * taw
    if dr <= raw:
        return 1.0

    return max(0.0, (taw - dr) / ((1.0 - p) * taw))


def vpd_stress(vpd: float) -> bool:
    """Return True when VPD ≥ 2 kPa — independent stomatal stress channel.

    High VPD triggers hazelnut stomatal closure even with full soil water.
    This channel must NEVER be folded into soil-only Ks.  Iron rule 3.

    Note: FAO-56 folds VPD into ET0 via (es−ea); it does not define a stomatal
    closure threshold. The 2 kPa value is from Egea et al. (2017) only.
    """
    if math.isnan(vpd):
        raise ValueError("VPD must not be NaN — check sensor/forecast pipeline")
    return vpd >= VPD_STRESS_THRESHOLD


def etc_adj(ks: float, kc: float, et0: float) -> float:
    """Stress-adjusted crop evapotranspiration in mm/day.  FAO-56 Eq.80.

    ETc_adj = Ks · Kc · ET0
    """
    return ks * kc * et0
