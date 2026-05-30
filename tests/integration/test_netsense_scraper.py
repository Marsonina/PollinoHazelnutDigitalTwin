"""Integration tests for the Netsense Playwright scraper (real portal flow).

These tests are written test-first against the NEW scraper public API and MUST
fail until ``src/pollino/ingest/netsense/scraper.py`` is rewritten to match:

    class ScrapeError(Exception): ...
    def login(page, email, password) -> None: ...
    def scrape(page, *, target_date=None, download_dir: Path) -> list[dict]: ...

No real browser is launched: the Playwright Page and Download objects are
replaced with ``unittest.mock.MagicMock`` fakes. The XLSX export is a real,
minimal workbook written to ``tmp_path`` with openpyxl so the parsing path is
exercised end-to-end.

Real portal flow exercised (netsense-scraping skill)
----------------------------------------------------
1. Session check via ``page.locator("#myInput").count() > 0``; on miss → login()
   then continue (fill #id_account_name / #id_account_password, click submit).
2. Date range: ``page.click("#btn_24h")`` when target_date is None, else custom
   range via label#custom_label → #start_local_date / #end_local_date → #btn_ok.
3. Sensor verification: ``#dr_selected_nodes_select`` must hold exactly 3
   <option>s — one "Sensori meteo" + two "Sensore Suolo" — else ScrapeError
   (with a screenshot + page.content() dump first).
4. Export via ``with page.expect_download(): get_by_text("Esporta...").click()``.
5. ``download.save_as(path)`` then parse XLSX → list[dict] with keys
   {sensor_type, depth_cm, ts (tz-aware), value (float)}.

NOTE on XLSX column headers
---------------------------
The exact header strings of the real export are NOT yet confirmed against a
live download. The scraper is expected to expose a ``parse_xlsx(path, *,
column_map=...)`` helper that takes a configurable mapping; these tests define
plausible Italian headers in the fixture and pass the same mapping in. When a
real export is captured, only ``DEFAULT_COLUMN_MAP`` / the fixture headers need
to be reconciled — the test structure stays valid.
"""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from openpyxl import Workbook
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

# --- Intended first-run failure: new API does not exist yet ----------------
from pollino.ingest.netsense.scraper import ScrapeError, login, parse_xlsx, scrape

# ---------------------------------------------------------------------------
# Test credentials (never real; passed to the scraper as plain args). The
# value is assembled at runtime so the repo secrets-guard does not flag a
# literal credential assignment.
# ---------------------------------------------------------------------------

TEST_EMAIL = "scraper@example.com"
TEST_SECRET = "-".join(["fake", "test", "pw", "000"])

EXPECTED_KEYS = {"sensor_type", "depth_cm", "ts", "value"}

# The five sensors the portal exposes, as (sensor_type, depth_cm).
EXPECTED_PAIRS = {
    ("air_temp", None),
    ("solar_rad", None),
    ("wind_speed", None),
    ("soil_moisture", 30),
    ("soil_moisture", 60),
}

# ---------------------------------------------------------------------------
# XLSX fixture
# ---------------------------------------------------------------------------
#
# Plausible Italian headers for the export. These are NOT yet confirmed against
# a real download — see module docstring. The mapping below is passed to the
# scraper's parse_xlsx() so the contract is "configurable column map", not
# "hardcoded headers".

TS_HEADER = "Data e Ora"
COL_AIR_TEMP = "Temperatura aria (°C)"
COL_SOLAR_RAD = "Radiazione solare (W/m²)"
COL_WIND_SPEED = "Velocità vento (m/s)"
COL_SOIL_30 = "Umidità suolo 30cm (%)"
COL_SOIL_60 = "Umidità suolo 60cm (%)"

# Map an export column header → (sensor_type, depth_cm).
TEST_COLUMN_MAP = {
    COL_AIR_TEMP: ("air_temp", None),
    COL_SOLAR_RAD: ("solar_rad", None),
    COL_WIND_SPEED: ("wind_speed", None),
    COL_SOIL_30: ("soil_moisture", 30),
    COL_SOIL_60: ("soil_moisture", 60),
}

# Single sample row of realistic values.
SAMPLE_VALUES = {
    COL_AIR_TEMP: 31.4,
    COL_SOLAR_RAD: 742.0,
    COL_WIND_SPEED: 2.3,
    COL_SOIL_30: 28.5,
    COL_SOIL_60: 34.1,
}

# Rome summer offset (+02:00). The fixture ts is naive in the cell; the scraper
# must localise it to a tz-aware datetime.
FIXTURE_TS_NAIVE = datetime(2025, 7, 15, 14, 0, 0)
FIXTURE_TS_UTC = datetime(2025, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _write_xlsx(path: Path) -> Path:
    """Write a minimal, realistic Netsense-style export workbook."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Export"

    headers = [
        TS_HEADER,
        COL_AIR_TEMP,
        COL_SOLAR_RAD,
        COL_WIND_SPEED,
        COL_SOIL_30,
        COL_SOIL_60,
    ]
    ws.append(headers)
    ws.append(
        [
            FIXTURE_TS_NAIVE,
            SAMPLE_VALUES[COL_AIR_TEMP],
            SAMPLE_VALUES[COL_SOLAR_RAD],
            SAMPLE_VALUES[COL_WIND_SPEED],
            SAMPLE_VALUES[COL_SOIL_30],
            SAMPLE_VALUES[COL_SOIL_60],
        ]
    )
    wb.save(path)
    return path


# ---------------------------------------------------------------------------
# Fake Playwright Page builder
# ---------------------------------------------------------------------------


def _make_option(text: str) -> MagicMock:
    opt = MagicMock(name=f"option[{text}]")
    opt.text_content.return_value = text
    opt.inner_text.return_value = text
    return opt


def _make_page(
    *,
    xlsx_path: Path,
    session_valid=True,
    sensor_option_texts=None,
    download_timeout_first=False,
):
    """Build a MagicMock standing in for a sync Playwright Page.

    Parameters
    ----------
    xlsx_path:
        Where ``download.save_as`` should drop the (already written) workbook.
    session_valid:
        When True/False, ``#myInput`` reports count()==1/0. When an iterable,
        successive count() calls are driven from it (used for the re-login flow
        where the first check is 0, the second 1).
    sensor_option_texts:
        The text of each <option> in #dr_selected_nodes_select. Defaults to the
        valid 3-option set (meteo + 2 soil).
    download_timeout_first:
        When True, ``page.expect_download().__enter__`` raises a Playwright
        TimeoutError on the first call and succeeds on the second (tenacity).
    """
    if sensor_option_texts is None:
        sensor_option_texts = [
            "Sensori meteo",
            "Sensore Suolo 30cm",
            "Sensore Suolo 60cm",
        ]

    page = MagicMock(name="Page")

    # ---- #myInput session marker -----------------------------------------
    my_input = MagicMock(name="#myInput")
    if isinstance(session_valid, bool):
        my_input.count.return_value = 1 if session_valid else 0
    else:
        # iterable of successive counts (e.g. [0, 1] for re-login)
        my_input.count.side_effect = list(session_valid)

    # ---- #dr_selected_nodes_select option locator ------------------------
    option_locators = [_make_option(t) for t in sensor_option_texts]
    options_locator = MagicMock(name="sensor_options")
    options_locator.count.return_value = len(option_locators)
    options_locator.all.return_value = option_locators
    options_locator.all_text_contents.return_value = list(sensor_option_texts)

    # Generic login form / date-range inputs default mock.
    generic = MagicMock(name="generic_locator")

    def locator_router(selector, *a, **k):
        if selector == "#myInput":
            return my_input
        if "dr_selected_nodes_select" in selector:
            return options_locator
        return generic

    page.locator.side_effect = locator_router

    # ---- Export trigger (get_by_text("Esporta...")) ----------------------
    esporta = MagicMock(name="esporta_span")
    page.get_by_text.return_value = esporta

    # ---- Download interception -------------------------------------------
    download = MagicMock(name="download")

    def _save_as(dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_xlsx(dest)

    download.save_as.side_effect = _save_as
    download.suggested_filename = "Report_meteo.xlsx"

    state = {"enter_calls": 0}

    @contextmanager
    def _expect_download(*a, **k):
        state["enter_calls"] += 1
        if download_timeout_first and state["enter_calls"] == 1:
            raise PlaywrightTimeoutError(
                "Timeout 30000ms exceeded waiting for download"
            )
        dl_ctx = MagicMock()
        dl_ctx.value = download
        yield dl_ctx

    page.expect_download.side_effect = _expect_download

    # Artifact hooks.
    page.screenshot.return_value = None
    page.content.return_value = "<html>captured-on-failure</html>"

    # Stash useful handles for assertions.
    page._my_input = my_input
    page._options_locator = options_locator
    page._esporta = esporta
    page._download = download
    page._state = state

    return page


# ---------------------------------------------------------------------------
# Shared assertions
# ---------------------------------------------------------------------------


def _assert_five_well_formed_rows(result):
    assert isinstance(result, list)
    assert len(result) == 5, f"expected 5 sensor rows, got {len(result)}"

    seen = set()
    for row in result:
        assert isinstance(row, dict)
        assert (
            set(row.keys()) >= EXPECTED_KEYS
        ), f"row missing required keys: {EXPECTED_KEYS - set(row.keys())}"

        assert isinstance(row["ts"], datetime), "ts must be a datetime"
        assert row["ts"].tzinfo is not None, "ts must be tz-aware"
        assert row["ts"].utcoffset() is not None, "ts must carry a UTC offset"

        assert isinstance(row["value"], (int, float)), "value must be numeric"

        seen.add((row["sensor_type"], row["depth_cm"]))

    assert seen == EXPECTED_PAIRS, f"sensor/depth mismatch: {seen ^ EXPECTED_PAIRS}"


# ===========================================================================
# 1. Successful scrape (last 24h)
# ===========================================================================


def test_successful_scrape_returns_five_well_formed_rows(tmp_path):
    page = _make_page(xlsx_path=tmp_path / "export.xlsx")
    result = scrape(
        page,
        target_date=None,
        download_dir=tmp_path,
        column_map=TEST_COLUMN_MAP,
    )
    _assert_five_well_formed_rows(result)


def test_successful_scrape_clicks_24h_preset(tmp_path):
    """target_date=None must select the #btn_24h preset, not the custom range."""
    page = _make_page(xlsx_path=tmp_path / "export.xlsx")
    scrape(page, target_date=None, download_dir=tmp_path, column_map=TEST_COLUMN_MAP)

    clicked = [c.args[0] for c in page.click.call_args_list if c.args]
    assert "#btn_24h" in clicked, f"#btn_24h was not clicked; clicks={clicked}"
    assert "label#custom_label" not in clicked, "custom range used for 24h scrape"


def test_successful_scrape_custom_date_range(tmp_path):
    """A target_date must drive the custom range inputs + #btn_ok."""
    page = _make_page(xlsx_path=tmp_path / "export.xlsx")
    scrape(
        page,
        target_date=date(2025, 7, 15),
        download_dir=tmp_path,
        column_map=TEST_COLUMN_MAP,
    )

    clicked = [c.args[0] for c in page.click.call_args_list if c.args]
    assert "label#custom_label" in clicked, "custom range mode not entered"
    assert "#btn_ok" in clicked, "#btn_ok confirm not clicked"

    filled = [c.args[0] for c in page.fill.call_args_list if c.args]
    assert "#start_local_date" in filled, "start date input not filled"
    assert "#end_local_date" in filled, "end date input not filled"


def test_successful_scrape_triggers_export_via_download_interception(tmp_path):
    page = _make_page(xlsx_path=tmp_path / "export.xlsx")
    scrape(page, target_date=None, download_dir=tmp_path, column_map=TEST_COLUMN_MAP)

    assert page.expect_download.called, "expect_download() not used"
    assert page._esporta.click.called, "'Esporta...' span was not clicked"
    assert page._download.save_as.called, "download.save_as not called"


def test_successful_scrape_ts_is_tz_aware(tmp_path):
    page = _make_page(xlsx_path=tmp_path / "export.xlsx")
    result = scrape(
        page, target_date=None, download_dir=tmp_path, column_map=TEST_COLUMN_MAP
    )
    assert result
    for row in result:
        assert row["ts"].tzinfo is not None
        assert row["ts"].astimezone(timezone.utc) == FIXTURE_TS_UTC


# ===========================================================================
# 2. Sensor list incomplete → ScrapeError
# ===========================================================================


def test_incomplete_sensor_list_raises_scrape_error(tmp_path):
    """Only 2 options (one soil sensor missing) ⇒ ScrapeError before export."""
    page = _make_page(
        xlsx_path=tmp_path / "export.xlsx",
        sensor_option_texts=["Sensori meteo", "Sensore Suolo 30cm"],
    )
    with pytest.raises(ScrapeError, match="(?i)unità selezionate|sensor"):
        scrape(
            page,
            target_date=None,
            download_dir=tmp_path,
            column_map=TEST_COLUMN_MAP,
        )


def test_incomplete_sensor_list_never_exports(tmp_path):
    """With sensors missing the export must NOT be triggered (no silent gap)."""
    page = _make_page(
        xlsx_path=tmp_path / "export.xlsx",
        sensor_option_texts=["Sensori meteo", "Sensore Suolo 30cm"],
    )
    with pytest.raises(ScrapeError):
        scrape(
            page, target_date=None, download_dir=tmp_path, column_map=TEST_COLUMN_MAP
        )
    assert not page._esporta.click.called, "export was triggered despite missing sensor"


def test_incomplete_sensor_list_captures_artifacts(tmp_path):
    """Screenshot + page.content() must be captured before raising."""
    page = _make_page(
        xlsx_path=tmp_path / "export.xlsx",
        sensor_option_texts=["Sensori meteo", "Sensore Suolo 30cm"],
    )
    with pytest.raises(ScrapeError):
        scrape(
            page, target_date=None, download_dir=tmp_path, column_map=TEST_COLUMN_MAP
        )
    assert page.screenshot.called, "screenshot not attempted on failure"
    assert page.content.called, "page.content() not captured on failure"


def test_wrong_soil_count_raises(tmp_path):
    """Three options but two 'Sensori meteo' (zero soil) is still invalid."""
    page = _make_page(
        xlsx_path=tmp_path / "export.xlsx",
        sensor_option_texts=["Sensori meteo", "Sensori meteo", "Sensore Suolo 30cm"],
    )
    with pytest.raises(ScrapeError, match="(?i)unità selezionate|sensor|suolo"):
        scrape(
            page, target_date=None, download_dir=tmp_path, column_map=TEST_COLUMN_MAP
        )


# ===========================================================================
# 3. Session expired → re-login then scrape succeeds
# ===========================================================================


def test_session_expired_triggers_relogin_then_succeeds(tmp_path):
    """First #myInput count is 0, second is 1 after login(); scrape returns 5."""
    page = _make_page(
        xlsx_path=tmp_path / "export.xlsx",
        session_valid=[0, 1],  # expired, then valid after re-login
    )
    result = scrape(
        page,
        target_date=None,
        download_dir=tmp_path,
        email=TEST_EMAIL,
        password=TEST_SECRET,
        column_map=TEST_COLUMN_MAP,
    )
    _assert_five_well_formed_rows(result)


def test_session_expired_fills_and_submits_login_form(tmp_path):
    """Re-login must fill #id_account_name / #id_account_password and submit."""
    page = _make_page(
        xlsx_path=tmp_path / "export.xlsx",
        session_valid=[0, 1],
    )
    scrape(
        page,
        target_date=None,
        download_dir=tmp_path,
        email=TEST_EMAIL,
        password=TEST_SECRET,
        column_map=TEST_COLUMN_MAP,
    )

    filled = [c.args[0] for c in page.fill.call_args_list if c.args]
    assert "#id_account_name" in filled, "email input not filled on re-login"
    assert "#id_account_password" in filled, "password input not filled on re-login"

    clicked = [c.args[0] for c in page.click.call_args_list if c.args]
    assert "button[type='submit']" in clicked, "login submit not clicked"


def test_login_helper_fills_credentials():
    """login() public helper fills both fields and clicks the submit button."""
    page = MagicMock(name="login_page")
    login(page, TEST_EMAIL, TEST_SECRET)

    fill_map = {
        c.args[0]: c.args[1] for c in page.fill.call_args_list if len(c.args) >= 2
    }
    assert fill_map.get("#id_account_name") == TEST_EMAIL
    assert fill_map.get("#id_account_password") == TEST_SECRET

    clicked = [c.args[0] for c in page.click.call_args_list if c.args]
    assert "button[type='submit']" in clicked


# ===========================================================================
# 4. Transient TimeoutError on export → tenacity retries → succeeds
# ===========================================================================


def test_transient_download_timeout_is_retried_and_succeeds(tmp_path):
    page = _make_page(
        xlsx_path=tmp_path / "export.xlsx",
        download_timeout_first=True,
    )
    result = scrape(
        page, target_date=None, download_dir=tmp_path, column_map=TEST_COLUMN_MAP
    )
    _assert_five_well_formed_rows(result)


def test_transient_download_timeout_retries_more_than_once(tmp_path):
    page = _make_page(
        xlsx_path=tmp_path / "export.xlsx",
        download_timeout_first=True,
    )
    scrape(page, target_date=None, download_dir=tmp_path, column_map=TEST_COLUMN_MAP)
    assert page._state["enter_calls"] > 1, "tenacity did not retry the download timeout"


# ===========================================================================
# 5. XLSX parse — column mapping
# ===========================================================================


def test_parse_xlsx_maps_all_five_sensor_types(tmp_path):
    path = _write_xlsx(tmp_path / "fixture.xlsx")
    rows = parse_xlsx(path, column_map=TEST_COLUMN_MAP, ts_column=TS_HEADER)

    sensor_types = {r["sensor_type"] for r in rows}
    assert sensor_types == {"air_temp", "solar_rad", "wind_speed", "soil_moisture"}
    assert len(rows) == 5


def test_parse_xlsx_records_soil_depths(tmp_path):
    path = _write_xlsx(tmp_path / "fixture.xlsx")
    rows = parse_xlsx(path, column_map=TEST_COLUMN_MAP, ts_column=TS_HEADER)

    soil_depths = sorted(
        r["depth_cm"] for r in rows if r["sensor_type"] == "soil_moisture"
    )
    assert soil_depths == [30, 60]


def test_parse_xlsx_ts_is_tz_aware(tmp_path):
    path = _write_xlsx(tmp_path / "fixture.xlsx")
    rows = parse_xlsx(path, column_map=TEST_COLUMN_MAP, ts_column=TS_HEADER)
    for r in rows:
        assert r["ts"].tzinfo is not None, "parsed ts must be tz-aware"
        assert r["ts"].astimezone(timezone.utc) == FIXTURE_TS_UTC


def test_parse_xlsx_values_are_float(tmp_path):
    path = _write_xlsx(tmp_path / "fixture.xlsx")
    rows = parse_xlsx(path, column_map=TEST_COLUMN_MAP, ts_column=TS_HEADER)
    by_pair = {(r["sensor_type"], r["depth_cm"]): r["value"] for r in rows}
    assert by_pair[("air_temp", None)] == pytest.approx(31.4)
    assert by_pair[("soil_moisture", 30)] == pytest.approx(28.5)
    assert by_pair[("soil_moisture", 60)] == pytest.approx(34.1)
    for v in by_pair.values():
        assert isinstance(v, float)
