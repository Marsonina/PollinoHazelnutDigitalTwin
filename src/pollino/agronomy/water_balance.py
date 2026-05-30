"""
FAO-56 soil-water balance — pure functions, no I/O.

All equations reference Allen et al. (1998) FAO Irrigation and Drainage Paper 56.
Hazelnut Kc: Egea et al. (2017); project default 0.95 (drip, mature, Piana di Cammarata).
"""

# Mid-season Kc for hazelnut — drip-irrigated mature orchard.
# Range 0.9–1.04 from Egea et al. (2017); 0.95 is the project scheduling default.
KC_HAZELNUT_MID: float = 0.95


def taw(theta_fc: float, theta_wp: float, zr: float) -> float:
    """Total available water in mm.  FAO-56 Eq.82.

    TAW = 1000·(θFC − θWP)·Zr
    Alluvial loam at Piana di Cammarata: θFC 0.28–0.32, θWP 0.12–0.18, Zr 0.6–1.0 m.
    """
    return 1000.0 * (theta_fc - theta_wp) * zr


def raw(taw_mm: float, p: float) -> float:
    """Readily available water in mm.  FAO-56 Eq.83.

    RAW = p·TAW
    """
    return p * taw_mm


def p_adjust(p_std: float, etc_mm: float) -> float:
    """Adjust depletion fraction p for actual ETc.  FAO-56 Table 22 footnote.

    p = p_std + 0.04·(5 − ETc), bounded [0.1, 0.8].
    """
    return min(0.8, max(0.1, p_std + 0.04 * (5.0 - etc_mm)))


def etc(kc: float, et0: float) -> float:
    """Crop evapotranspiration in mm/day.  FAO-56 Eq.58.

    ETc = Kc·ET0  (single-coefficient approach)
    """
    return kc * et0


def daily_depletion(
    dr_prev: float,
    precip: float,
    runoff: float,
    irrigation: float,
    cr: float,
    etc_mm: float,
    dp: float,
    taw_mm: float,
) -> float:
    """Daily root-zone depletion in mm.  FAO-56 Eq.85.

    Dr,i = Dr,i−1 − (P−RO) − I − CR + ETc + DP
    Clamped to [0, TAW]: Dr cannot be negative (excess leaves as deep percolation)
    nor exceed TAW (soil cannot hold more deficit than total available water).

    Parameters
    ----------
    dr_prev    : depletion at end of previous day (mm)
    precip     : precipitation (mm)
    runoff     : surface runoff (mm)
    irrigation : net irrigation (mm)
    cr         : capillary rise (mm), typically 0 for deep water table
    etc_mm     : crop evapotranspiration for the day (mm)
    dp         : deep percolation (mm)
    taw_mm     : total available water — upper clamp (mm)
    """
    dr = dr_prev - (precip - runoff) - irrigation - cr + etc_mm + dp
    return min(taw_mm, max(0.0, dr))


def kc_adjust(kc: float, u2: float, rh_min: float, h: float) -> float:
    """Climate-adjust Kc for non-standard wind / humidity conditions.  FAO-56 Eq.62.

    Kc_adj = Kc + [0.04·(u2−2) − 0.004·(RHmin−45)]·(h/3)^0.3

    Parameters
    ----------
    kc     : tabulated (unadjusted) Kc
    u2     : mean daily wind speed at 2 m during mid/late season (m/s)
    rh_min : mean daily minimum relative humidity during mid/late season (%)
    h      : mean crop height (m)
    """
    return kc + (0.04 * (u2 - 2.0) - 0.004 * (rh_min - 45.0)) * (h / 3.0) ** 0.3
