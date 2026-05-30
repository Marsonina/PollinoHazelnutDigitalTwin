---
name: tdd-workflow
description: Test-first discipline for this repo. Use at the start of any feature in the agronomy core or pipeline.
---
1. Write the failing test first (delegate to test-author). Run it; confirm it fails
   for the right reason.
2. Implement the minimum to pass. Run pytest. 3. Refactor; keep green.
4. Agronomy changes: every numeric must trace to fao56-water-balance or a cited source.
5. Heatwave regression fixture must stay green on every commit.
6. Coverage gate: agronomy/ modules ≥ 95%.
