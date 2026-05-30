---
name: netsense-scraping
description: Conventions for scraping the Netsense sensor portal (no public API). Use when building or debugging the Playwright ingestion for air temp, solar radiation, wind, and soil moisture at 30/60 cm.
---

## Portal

- Login URL: https://srv1.netsens.it/ (live.netsens.it/home.php redirects here)
- Auth fields: email (valid email format) + password (min 6 chars); submit button text "LOGIN"
- Station URL fragment: #3l0ci (Piana di Cammarata / CAMMARATA-01)
- Auth: persist Playwright storage_state to a path from NETSENSE_STORAGE_STATE env var
  (NEVER committed). Detect session expiry by asserting a known post-login selector;
  on miss, full re-login.
- Locators for login form (confirmed from DOM):
    Email    : page.fill("#id_account_name", email)      # name="account_name"
    Password : page.fill("#id_account_password", password) # name="account_password"
    Submit   : page.click("button[type='submit']")        # text "LOGIN", type=submit
  Credentials come from env vars NETSENSE_EMAIL and NETSENSE_PASSWORD (never hardcoded).
- Post-login session marker (confirmed from DOM):
    Locator : page.locator("#myInput")   # station autocomplete, only on dashboard
    Check   : page.locator("#myInput").count() > 0
    On miss → session expired → full re-login.

## Manual export flow (implemented by the scraper)

1. Navigate to the station page.
2. Set "Intervallo temporale" (date/time range control) to the desired window.
3. Confirm "Unità selezionate" contains the five expected sensors:
   air_temp, solar_rad, wind_speed, soil_moisture@30cm, soil_moisture@60cm.
   Raise ScrapeError if any are missing.
4. Click "Esporta" — triggers a spreadsheet download (XLSX, same format as
   "Report meteo giugno luglio 2025.xlsx").
5. Wait for the download to complete; return the local file path.
- Date range locators (confirmed from DOM):
    Switch to custom mode : page.click("label#custom_label")
    Start date input      : page.fill("#start_local_date", "YYYY-MM-DD")
    End date input        : page.fill("#end_local_date",   "YYYY-MM-DD")
    Confirm               : page.click("#btn_ok")
  Preset shortcuts (if ever needed): #btn_24h / #btn_3d / #btn_7d / #btn_30d
- Export trigger (confirmed from DOM): NOT a <button> — it's a <span onclick="dr_generate('1')">
    Locator : page.get_by_text("Esporta...")
    Must use Playwright download interception:
      with page.expect_download() as dl:
          page.get_by_text("Esporta...").click()
      download = dl.value
      download.save_as(local_path)
- Sensor list (confirmed from DOM):
    Container : #dr_selected_nodes_select  (a <select multiple>)
    Must contain exactly 3 <option> entries before exporting:
      - one matching "Sensori meteo"   (weather unit: air_temp, solar_rad, wind_speed)
      - two matching "Sensore Suolo"   (soil sensors: 30 cm and 60 cm)
    Verification pattern:
      opts = page.locator("#dr_selected_nodes_select option").all_text_contents()
      assert len(opts) == 3
      assert any("Sensori meteo" in o for o in opts)
      assert sum(1 for o in opts if "Sensore Suolo" in o) == 2
    If assertion fails → ScrapeError("Unità selezionate incomplete: …") before exporting.

## Output

- Parse the downloaded XLSX with openpyxl/pandas; emit:
  list[ {sensor_type, depth_cm, ts (tz-aware), value} ] for upsert.
- Record station code + depth on every row.

## Error handling

- On ANY failure: screenshot + page.content() dump to artifacts/, raise typed
  ScrapeError -> pipeline alerts. Never return empty silently.
- Confirm sensor list before exporting; missing sensor = ScrapeError, not silent gap.
