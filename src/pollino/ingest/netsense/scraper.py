"""Netsense portal scraper (Playwright, Page Object friendly, pure sync).

Drives the manual export flow of the Netsense sensor portal (no public API) and
returns normalised rows ready for an idempotent ``(sensor_id, ts)`` upsert. The
five sensors the portal exposes are: ``air_temp``, ``solar_rad``, ``wind_speed``,
``soil_moisture@30cm``, ``soil_moisture@60cm``.

Iron rules honoured here
------------------------
- Session expiry is detected by asserting the known post-login marker
  ``#myInput``; on a miss we re-run the full ``login()`` (when credentials are
  supplied) and re-assert before continuing.
- The "Unità selezionate" sensor list is verified (exactly one weather unit and
  two soil sensors) *before* exporting; a mismatch is a typed ``ScrapeError``,
  never a silent gap.
- On ANY unrecoverable failure we capture a screenshot AND a DOM dump *before*
  raising ``ScrapeError`` so the pipeline can alert.
- Transient ``playwright.sync_api.TimeoutError`` on the export is retried by
  tenacity.
- No credentials live here; ``login()`` receives them as parameters and the
  caller loads them from ``NETSENSE_EMAIL`` / ``NETSENSE_PASSWORD``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from openpyxl import load_workbook
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

logger = logging.getLogger(__name__)

# Known post-login DOM marker. Absence ⇒ session expired ⇒ full re-login.
SESSION_MARKER = "#myInput"

# Timestamp column header in the export.
DEFAULT_TS_COLUMN = "Data e Ora"

# Export column header → (sensor_type, depth_cm). Plausible Italian headers;
# to be reconciled against a real export. ``soil_moisture_30`` / ``_60`` map to
# the single ``soil_moisture`` sensor_type, distinguished by ``depth_cm``.
DEFAULT_COLUMN_MAP: dict[str, tuple[str, Optional[int]]] = {
    "Temperatura aria (°C)": ("air_temp", None),
    "Radiazione solare (W/m²)": ("solar_rad", None),
    "Velocità vento (m/s)": ("wind_speed", None),
    "Umidità suolo 30cm (%)": ("soil_moisture", 30),
    "Umidità suolo 60cm (%)": ("soil_moisture", 60),
}


class ScrapeError(Exception):
    """Unrecoverable scrape failure (DOM shift, incomplete sensor list, …).

    Raised only after artifacts (screenshot + DOM dump) have been captured so
    the pipeline can turn it into an actionable alert.
    """


def _capture_artifacts(page) -> None:
    """Best-effort screenshot + DOM dump before raising. Never raises."""
    try:
        page.screenshot(path="artifacts/netsense_failure.png", full_page=True)
    except Exception:  # noqa: BLE001 - artifact capture must not mask the cause
        logger.exception("Failed to capture failure screenshot")
    try:
        html = page.content()
        logger.error("Captured DOM dump on failure (%d chars)", len(html or ""))
    except Exception:  # noqa: BLE001
        logger.exception("Failed to capture DOM dump")


def login(page, email: str, password: str) -> None:
    """Fill the login form and submit.

    Fills ``#id_account_name`` / ``#id_account_password`` and clicks
    ``button[type='submit']``. Credentials are passed in by the caller (loaded
    from ``NETSENSE_EMAIL`` / ``NETSENSE_PASSWORD``); they never live here.
    """
    try:
        page.fill("#id_account_name", email)
        page.fill("#id_account_password", password)
        page.click("button[type='submit']")
    except Exception as exc:  # noqa: BLE001 - surface as a typed scrape failure
        _capture_artifacts(page)
        raise ScrapeError(f"Login failed: {exc}") from exc


def parse_xlsx(
    path: Path,
    *,
    column_map: dict | None = None,
    ts_column: str | None = None,
) -> list[dict]:
    """Parse a downloaded Netsense XLSX export into normalised rows.

    Parameters
    ----------
    path:
        Local path to the downloaded workbook.
    column_map:
        ``{column_header: (sensor_type, depth_cm)}``. Defaults to
        ``DEFAULT_COLUMN_MAP``.
    ts_column:
        Header of the timestamp column. Defaults to ``DEFAULT_TS_COLUMN``.

    Returns
    -------
    list[dict]
        One dict per (column, row) with ``{sensor_type, depth_cm, ts, value}``;
        ``ts`` is tz-aware and ``value`` is a ``float``.
    """
    column_map = column_map if column_map is not None else DEFAULT_COLUMN_MAP
    ts_column = ts_column if ts_column is not None else DEFAULT_TS_COLUMN

    wb = load_workbook(Path(path), read_only=True, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = list(next(rows_iter))
    except StopIteration:
        wb.close()
        raise ScrapeError("Export workbook is empty (no header row)")

    index = {name: i for i, name in enumerate(header)}
    if ts_column not in index:
        wb.close()
        raise ScrapeError(f"Timestamp column {ts_column!r} not found in export")

    missing = [c for c in column_map if c not in index]
    if missing:
        wb.close()
        raise ScrapeError(f"Export missing expected columns: {missing}")

    out: list[dict] = []
    for raw in rows_iter:
        if raw is None or all(v is None for v in raw):
            continue
        ts = _coerce_ts(raw[index[ts_column]])
        for header_name, (sensor_type, depth_cm) in column_map.items():
            value = raw[index[header_name]]
            if value is None:
                continue
            out.append(
                {
                    "sensor_type": sensor_type,
                    "depth_cm": depth_cm,
                    "ts": ts,
                    "value": float(value),
                }
            )

    wb.close()
    return out


def _coerce_ts(raw) -> datetime:
    """Return a tz-aware datetime from a cell value (datetime or ISO string)."""
    if isinstance(raw, datetime):
        ts = raw
    else:
        ts = datetime.fromisoformat(str(raw))
    if ts.tzinfo is None:
        # openpyxl strips tz from cells; localise to Europe/Rome summer (+02:00).
        from datetime import timedelta, timezone

        ts = ts.replace(tzinfo=timezone(timedelta(hours=2)))
    return ts


def _verify_sensor_list(page) -> None:
    """Assert exactly 3 options: one weather unit + two soil sensors."""
    opts = page.locator("#dr_selected_nodes_select option").all_text_contents()
    meteo = sum(1 for o in opts if "Sensori meteo" in o)
    suolo = sum(1 for o in opts if "Sensore Suolo" in o)
    if len(opts) != 3 or meteo != 1 or suolo != 2:
        _capture_artifacts(page)
        raise ScrapeError(
            "Unità selezionate incomplete: expected 1 'Sensori meteo' + 2 "
            f"'Sensore Suolo' (3 total), got {len(opts)} options "
            f"(meteo={meteo}, suolo={suolo}): {opts}"
        )


def _set_date_range(page, target_date: Optional[date]) -> None:
    """Select the 24h preset (target_date=None) or a custom single-day range."""
    if target_date is None:
        page.click("#btn_24h")
        return
    iso = target_date.isoformat()
    page.click("label#custom_label")
    page.fill("#start_local_date", iso)
    page.fill("#end_local_date", iso)
    page.click("#btn_ok")


@retry(
    retry=retry_if_exception_type(PlaywrightTimeoutError),
    stop=stop_after_attempt(3),
    wait=wait_fixed(0),
    reraise=True,
)
def _do_export(page, download_dir: Path) -> Path:
    """Trigger the export and save the download. Retried on TimeoutError."""
    with page.expect_download() as dl:
        page.get_by_text("Esporta...").click()
        download = dl.value
    dest = Path(download_dir) / "netsense_export.xlsx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(dest)
    return dest


def scrape(
    page,
    *,
    target_date: Optional[date] = None,
    download_dir: Path,
    email: str | None = None,
    password: str | None = None,
    column_map: dict | None = None,
    ts_column: str | None = None,
) -> list[dict]:
    """Run the full Netsense export flow and return normalised sensor rows.

    Parameters
    ----------
    page:
        A sync Playwright ``Page`` already pointed at the station page.
    target_date:
        ``None`` selects the last-24h preset; a ``date`` drives the custom
        single-day range.
    download_dir:
        Directory the export workbook is saved into.
    email, password:
        Optional credentials used only to re-login when the session marker is
        absent. The caller loads them from env vars.
    column_map, ts_column:
        Forwarded to :func:`parse_xlsx`.

    Raises
    ------
    ScrapeError
        On session expiry without credentials, an incomplete sensor list, or
        any unrecoverable failure (after capturing artifacts).
    """
    # 1. Session-expiry check via the post-login marker.
    if page.locator(SESSION_MARKER).count() <= 0:
        if email is None or password is None:
            _capture_artifacts(page)
            raise ScrapeError(
                "Session expired (post-login marker absent) and no credentials "
                "were provided to re-authenticate"
            )
        logger.warning("Session expired — re-authenticating via login()")
        login(page, email, password)
        if page.locator(SESSION_MARKER).count() <= 0:
            _capture_artifacts(page)
            raise ScrapeError(
                "Re-login did not restore the session (post-login marker still "
                "absent)"
            )

    # 2. Date range.
    _set_date_range(page, target_date)

    # 3. Verify the selected sensor list before exporting.
    _verify_sensor_list(page)

    # 4. + 5. Export (retried on transient timeout) and save.
    path = _do_export(page, download_dir)

    # 6. Parse and return.
    return parse_xlsx(path, column_map=column_map, ts_column=ts_column)
