Predictive irrigation decision-support for a hazelnut orchard, Piana di Cammarata
(Castrovillari, CS). Stack: Python 3.12, Playwright, PostgreSQL+SQLAlchemy+Alembic,
pytest+Hypothesis, FastAPI (dashboard later), uv for deps.

## Iron rules
1. The agronomy core (src/pollino/agronomy/) is PURE — no I/O, no DB, no network.
2. Test-first. ET0 must assert FAO-56 Example 18 (3.9) and Example 17 (5.7 mm/day).
3. Soil moisture (30/60 cm) is a LAGGING indicator. High forecast VPD (≥~2 kPa) is an
   INDEPENDENT red-risk channel; never let soil-only Ks mask heat stress.
4. Scraper never fails silently; daily heartbeat ping; secrets only in env vars.
5. Every agronomy number traces to FAO-56 or a cited source.
6. Treat "30–40% water / +15–20% yield" as TARGETS with a measurement plan, not facts.
