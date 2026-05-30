---
name: fao56-water-balance
description: Canonical FAO-56 equations and parameters for the hazelnut water balance. Use whenever computing ET0, ETc, soil-water depletion Dr, TAW/RAW, or the Ks stress coefficient, or when writing tests for them.
---
## Reference ET (FAO-56 Eq.6, daily)
ET0 = [0.408·Δ·(Rn−G) + γ·(900/(T+273))·u2·(es−ea)] / [Δ + γ·(1+0.34·u2)]
- e°(T)=0.6108·exp(17.27T/(T+237.3)); es=(e°(Tmax)+e°(Tmin))/2
- ea=(e°(Tmin)·RHmax/100 + e°(Tmax)·RHmin/100)/2 ; VPD=es−ea
- Δ=4098·e°(T)/(T+237.3)² ; P=101.3·((293−0.0065z)/293)^5.26 ; γ=0.000665·P
- u2=u10·4.87/ln(67.8·10−5.42) ; Rn=Rns−Rnl, Rns=(1−0.23)Rs
- G≈0 for daily step.
## ORACLES (assert in tests)
- Example 18 (Brussels): ET0 ≈ 3.9 mm/day. Intermediates: u2=2.078, P=100.1,
  Tmean=16.9, Δ=0.122, γ=0.0666, es=1.997, ea=1.409, VPD=0.589, Rn=13.28.
- Example 17 (Bangkok): ET0 ≈ 5.7 mm/day.
## Crop ET & balance
- ETc=Kc·ET0 (single) or (Kcb+Ke)·ET0 (dual).
- Hazelnut Kc (NO FAO entry): mid-season 0.9–1.04 mature drip; FAO default 0.9
  overestimates at low density. Climate-adjust:
  Kc_adj = Kc + [0.04(u2−2) − 0.004(RHmin−45)]·(h/3)^0.3
- Daily depletion (Eq.85): Dr,i = Dr,i−1 − (P−RO) − I − CR + ETc + DP ; clamp 0≤Dr≤TAW.
- TAW=1000(θFC−θWP)·Zr ; RAW=p·TAW ; p≈0.50 (walnut analog), p=p+0.04(5−ETc) bounded.
- Alluvial loam: θFC≈0.28–0.32, θWP≈0.12–0.18; effective Zr≈0.6–1.0 m for scheduling.
## Stress (Eq.84)
- Dr≤RAW ⇒ Ks=1 ; Dr>RAW ⇒ Ks=(TAW−Dr)/((1−p)·TAW). ETc_adj=Ks·Kc·ET0.
- High VPD (≥~2 kPa) = INDEPENDENT stress channel; hazelnut closes stomata at high
  VPD even with full soil water. Never let soil-only Ks mask this.
