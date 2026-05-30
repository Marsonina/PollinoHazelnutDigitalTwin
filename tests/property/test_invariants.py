"""
Hypothesis property tests for the agronomy core.

These invariants must hold for ALL physically valid inputs, not just the FAO-56
oracle values tested in tests/unit/. Hypothesis searches for counterexamples and
shrinks any failing case to its minimal form.

Invariants:
  Ks ∈ [0, 1]                              FAO-56 Eq.84
  Ks non-increasing with Dr                FAO-56 Eq.84
  ET₀ ≥ 0 when Rn ≥ 0                     FAO-56 Eq.6
  Dr ∈ [0, TAW] after every daily step     FAO-56 Eq.85 clamp
  Dr non-decreasing with no water input    FAO-56 Eq.85
  risk ∈ {"G", "Y", "R"}                  traffic_light, hysteresis, lookahead
  VPD ≥ 2 kPa always yields "R"           iron rule 3
  ETc_adj ≤ Kc·ET₀ (stress never raises ET)  FAO-56 Eq.80
"""

from hypothesis import assume, given
from hypothesis import strategies as st

from pollino.agronomy.et0 import et0_penman_monteith
from pollino.agronomy.risk import (
    risk_lookahead,
    traffic_light,
    traffic_light_hysteresis,
)
from pollino.agronomy.stress import VPD_STRESS_THRESHOLD, etc_adj, ks
from pollino.agronomy.water_balance import daily_depletion

# ---------------------------------------------------------------------------
# Shared strategies — physically meaningful ranges for Piana di Cammarata
# ---------------------------------------------------------------------------

s_temp = st.floats(min_value=-20.0, max_value=55.0, allow_nan=False)
s_rh = st.floats(min_value=0.0, max_value=100.0, allow_nan=False)
s_u2 = st.floats(min_value=0.0, max_value=20.0, allow_nan=False)
s_rn_pos = st.floats(min_value=0.0, max_value=40.0, allow_nan=False)
s_elev = st.floats(min_value=0.0, max_value=4000.0, allow_nan=False)

s_taw = st.floats(min_value=1.0, max_value=400.0, allow_nan=False)
s_p = st.floats(min_value=0.0, max_value=0.99, allow_nan=False)
s_mm_pos = st.floats(min_value=0.0, max_value=100.0, allow_nan=False)
s_vpd_pos = st.floats(min_value=0.0, max_value=10.0, allow_nan=False)
s_kc = st.floats(min_value=0.0, max_value=1.5, allow_nan=False)
s_et0 = st.floats(min_value=0.0, max_value=15.0, allow_nan=False)
s_prev = st.sampled_from(["G", "Y", "R"])
s_level = st.sampled_from(["G", "Y", "R"])


# ---------------------------------------------------------------------------
# Ks — FAO-56 Eq.84
# ---------------------------------------------------------------------------


@given(
    dr=st.floats(min_value=0.0, max_value=400.0, allow_nan=False),
    taw=s_taw,
    p=s_p,
)
def test_ks_always_in_unit_interval(dr, taw, p):
    """Ks ∈ [0, 1] for all physically valid (dr, taw, p).  FAO-56 Eq.84."""
    assume(dr <= taw)
    result = ks(dr=dr, taw=taw, p=p)
    assert 0.0 <= result <= 1.0


@given(
    dr_lo=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
    dr_hi=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
    taw=s_taw,
    p=s_p,
)
def test_ks_non_increasing_with_depletion(dr_lo, dr_hi, taw, p):
    """Higher depletion yields lower or equal Ks — monotone non-increasing.  FAO-56 Eq.84."""
    assume(dr_lo <= dr_hi <= taw)
    assert ks(dr=dr_lo, taw=taw, p=p) >= ks(dr=dr_hi, taw=taw, p=p)


# ---------------------------------------------------------------------------
# ET₀ — FAO-56 Eq.6
# ---------------------------------------------------------------------------


@given(
    t_max=s_temp,
    t_min=s_temp,
    rh_max=s_rh,
    rh_min=s_rh,
    u2=s_u2,
    rn=s_rn_pos,
    z=s_elev,
)
def test_et0_non_negative_when_rn_non_negative(t_max, t_min, rh_max, rh_min, u2, rn, z):
    """ET₀ ≥ 0 for all physically valid inputs with Rn ≥ 0.  FAO-56 Eq.6.

    With Rn ≥ 0 the radiation term is non-negative and the aerodynamic term
    (γ·(900/(T+273))·u2·VPD) is also non-negative (VPD = es−ea ≥ 0), so the
    Penman-Monteith numerator is non-negative and the denominator is strictly
    positive — hence ET₀ ≥ 0.
    """
    assume(t_max > t_min)
    assume(rh_max >= rh_min)
    result = et0_penman_monteith(
        t_max=t_max,
        t_min=t_min,
        rh_max=rh_max,
        rh_min=rh_min,
        u2=u2,
        rn=rn,
        z=z,
    )
    assert result >= 0.0


# ---------------------------------------------------------------------------
# daily_depletion — FAO-56 Eq.85 clamping and monotonicity
# ---------------------------------------------------------------------------


@given(
    dr_prev=st.floats(min_value=0.0, max_value=400.0, allow_nan=False),
    precip=s_mm_pos,
    runoff=s_mm_pos,
    irrigation=s_mm_pos,
    cr=s_mm_pos,
    etc_mm=s_mm_pos,
    dp=s_mm_pos,
    taw_mm=s_taw,
)
def test_daily_depletion_always_in_range(
    dr_prev, precip, runoff, irrigation, cr, etc_mm, dp, taw_mm
):
    """Dr ∈ [0, TAW] after every daily step.  FAO-56 Eq.85 clamp."""
    assume(dr_prev <= taw_mm)
    assume(runoff <= precip)  # runoff cannot exceed precipitation
    result = daily_depletion(
        dr_prev=dr_prev,
        precip=precip,
        runoff=runoff,
        irrigation=irrigation,
        cr=cr,
        etc_mm=etc_mm,
        dp=dp,
        taw_mm=taw_mm,
    )
    assert 0.0 <= result <= taw_mm


@given(
    dr_init=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
    taw=s_taw,
    etc_mm=st.floats(min_value=0.01, max_value=15.0, allow_nan=False),
    n_days=st.integers(min_value=1, max_value=30),
)
def test_dr_non_decreasing_with_no_water(dr_init, taw, etc_mm, n_days):
    """Dr never decreases when no water is added (precip=irrigation=0).  FAO-56 Eq.85.

    This is the drying-only invariant: Dr can only grow or stay flat (at TAW).
    """
    assume(dr_init <= taw)
    dr = dr_init
    for _ in range(n_days):
        dr_new = daily_depletion(
            dr_prev=dr,
            precip=0.0,
            runoff=0.0,
            irrigation=0.0,
            cr=0.0,
            etc_mm=etc_mm,
            dp=0.0,
            taw_mm=taw,
        )
        assert dr_new >= dr
        dr = dr_new


# ---------------------------------------------------------------------------
# risk ∈ {"G", "Y", "R"}
# ---------------------------------------------------------------------------


@given(
    dr=st.floats(min_value=0.0, max_value=400.0, allow_nan=False),
    raw=st.floats(min_value=0.1, max_value=200.0, allow_nan=False),
    mad=st.floats(min_value=0.1, max_value=400.0, allow_nan=False),
)
def test_traffic_light_always_valid_level(dr, raw, mad):
    """traffic_light always returns a member of {G, Y, R}."""
    assume(raw < mad)
    assert traffic_light(dr=dr, raw=raw, mad=mad) in {"G", "Y", "R"}


@given(
    dr=st.floats(min_value=0.0, max_value=400.0, allow_nan=False),
    raw=st.floats(min_value=0.1, max_value=200.0, allow_nan=False),
    mad=st.floats(min_value=0.1, max_value=400.0, allow_nan=False),
    prev=s_prev,
    band=st.floats(min_value=0.0, max_value=20.0, allow_nan=False),
)
def test_traffic_light_hysteresis_always_valid_level(dr, raw, mad, prev, band):
    """traffic_light_hysteresis always returns a member of {G, Y, R}."""
    assume(raw < mad)
    assert traffic_light_hysteresis(
        dr=dr,
        raw=raw,
        mad=mad,
        prev_level=prev,
        hysteresis_mm=band,
    ) in {"G", "Y", "R"}


@given(
    dr=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
    raw=st.floats(min_value=1.0, max_value=100.0, allow_nan=False),
    mad=st.floats(min_value=1.0, max_value=200.0, allow_nan=False),
    taw=st.floats(min_value=50.0, max_value=400.0, allow_nan=False),
    vpd=s_vpd_pos,
    etc_day=st.floats(min_value=0.0, max_value=15.0, allow_nan=False),
    precip_day=s_mm_pos,
    n_days=st.integers(min_value=1, max_value=2),
)
def test_risk_lookahead_always_valid_level(
    dr, raw, mad, taw, vpd, etc_day, precip_day, n_days
):
    """risk_lookahead always returns a member of {G, Y, R}."""
    assume(raw < mad <= taw)
    assume(dr <= taw)
    result = risk_lookahead(
        dr=dr,
        raw=raw,
        mad=mad,
        taw=taw,
        forecast_etc=[etc_day] * n_days,
        forecast_precip=[precip_day] * n_days,
        vpd=vpd,
    )
    assert result in {"G", "Y", "R"}


# ---------------------------------------------------------------------------
# Iron rule 3 — VPD ≥ 2 kPa always yields RED (independent of soil state)
# ---------------------------------------------------------------------------


@given(
    dr=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
    raw=st.floats(min_value=1.0, max_value=100.0, allow_nan=False),
    mad=st.floats(min_value=1.0, max_value=200.0, allow_nan=False),
    taw=st.floats(min_value=50.0, max_value=400.0, allow_nan=False),
    vpd=st.floats(min_value=VPD_STRESS_THRESHOLD, max_value=10.0, allow_nan=False),
    etc_day=st.floats(min_value=0.0, max_value=15.0, allow_nan=False),
    precip_day=s_mm_pos,
    n_days=st.integers(min_value=1, max_value=2),
)
def test_vpd_above_threshold_always_red(
    dr, raw, mad, taw, vpd, etc_day, precip_day, n_days
):
    """VPD ≥ 2 kPa always produces RED regardless of soil depletion.  Iron rule 3."""
    assume(raw < mad <= taw)
    assume(dr <= taw)
    result = risk_lookahead(
        dr=dr,
        raw=raw,
        mad=mad,
        taw=taw,
        forecast_etc=[etc_day] * n_days,
        forecast_precip=[precip_day] * n_days,
        vpd=vpd,
    )
    assert result == "R", (
        f"Expected R for VPD={vpd:.3f} ≥ {VPD_STRESS_THRESHOLD} kPa, "
        f"but got {result!r} (dr={dr:.1f}, raw={raw:.1f}, mad={mad:.1f})"
    )


# ---------------------------------------------------------------------------
# ETc_adj — FAO-56 Eq.80: stress never raises actual ET above potential
# ---------------------------------------------------------------------------


@given(
    ks_val=st.floats(min_value=0.0, max_value=1.0, allow_nan=False), kc=s_kc, et0=s_et0
)
def test_etc_adj_never_exceeds_unstressed_etc(ks_val, kc, et0):
    """ETc_adj = Ks·Kc·ET₀ ≤ Kc·ET₀ (stress only reduces actual ET).  FAO-56 Eq.80."""
    adj = etc_adj(ks=ks_val, kc=kc, et0=et0)
    unstressed = kc * et0
    assert adj <= unstressed + 1e-9  # tolerance for floating-point equality at Ks=1
