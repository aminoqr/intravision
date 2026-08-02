"""The renderer. Reads the store, never the 42 API.

That separation is deliberate: page loads cost zero API quota, and the TV keeps
showing the last good data when the 42 intranet is down.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Config
from .store import Store

log = logging.getLogger("ft.app")

cfg = Config.from_env()
store = Store(cfg.db_path)
_PKG = Path(__file__).parent
templates = Jinja2Templates(directory=str(_PKG / "templates"))

app = FastAPI(title="42 Warsaw Dashboard", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(_PKG / "static")), name="static")

# Guards against overlapping refreshes if someone leans on the button.
_refresh_lock = threading.Lock()

EMPTY: dict[str, Any] = {
    "generated_at": None,
    "pulse": {
        "on_campus": 0,
        "validated_this_week": 0,
        "active_students": 0,
        "median_level": 0,
    },
    "active_on_campus": [],
    "cluster_capacity": [],
    "recent_validations": [],
    "project_popularity": [],
    "level_distribution": [],
    "coalitions": [],
    "level_ups": [],
    "average_milestone": 0,
    "average_session": 0,
    "weekly_passes": 0,
    "evals_completed": 0,
    "zombies": [],
    "active_project_data": [],
    "top_evaluators": [],
    "weekly_logtime_data": [],
}


def current_metrics() -> dict[str, Any]:
    return store.get("metrics", default=EMPTY) or EMPTY


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "metrics": current_metrics(),
            "last_refresh": store.get_meta("last_refresh"),
            "refresh_seconds": cfg.refresh_seconds,
        },
    )


@app.get("/api/metrics")
def api_metrics() -> JSONResponse:
    """Polled by the TV page every 30s. Local read, no API quota."""
    return JSONResponse(
        {
            "metrics": current_metrics(),
            "last_refresh": store.get_meta("last_refresh"),
        }
    )


@app.get("/api/weather")
def api_weather() -> JSONResponse:
    """Warsaw temperature via Open-Meteo — no API key, zero Intra quota."""
    try:
        import httpx

        # Warsaw Social Space coordinates (approx. campus).
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=52.2297&longitude=21.0122"
            "&current=temperature_2m"
            "&timezone=Europe%2FWarsaw"
        )
        with httpx.Client(timeout=8.0) as client:
            payload = client.get(url).json()
        temp = (payload.get("current") or {}).get("temperature_2m")
        if temp is None:
            return JSONResponse({"ok": False, "temp_c": None, "label": "—°C"})
        rounded = int(round(float(temp)))
        return JSONResponse(
            {
                "ok": True,
                "temp_c": rounded,
                "label": f"{rounded}°C",
                "city": "Warsaw",
            }
        )
    except Exception as exc:  # noqa: BLE001 — weather must never break the TV
        log.warning("weather fetch failed: %s", exc)
        return JSONResponse(
            {"ok": False, "temp_c": None, "label": "—°C", "city": "Warsaw"},
            status_code=200,
        )


def _run_refresh() -> None:
    if not _refresh_lock.acquire(blocking=False):
        log.info("refresh already running, skipping")
        return
    try:
        from .fetch import refresh  # imported lazily so the renderer never needs the client

        refresh(cfg)
    except Exception as exc:  # noqa: BLE001 - a failed refresh must not kill the server
        log.error("refresh failed: %s", exc)
    finally:
        _refresh_lock.release()


@app.post("/refresh")
def trigger_refresh(background: BackgroundTasks) -> JSONResponse:
    """The brief's required refresh option. Returns immediately; fetch runs behind it."""
    background.add_task(_run_refresh)
    return JSONResponse({"status": "refresh scheduled"}, status_code=202)


@app.get("/healthz")
def healthz() -> JSONResponse:
    last = store.get_meta("last_refresh")
    return JSONResponse({"ok": True, "last_refresh": last, "has_data": last is not None})
