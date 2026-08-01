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
from fastapi.templating import Jinja2Templates

from .config import Config
from .store import Store

log = logging.getLogger("ft.app")

cfg = Config.from_env()
store = Store(cfg.db_path)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="42 Warsaw Dashboard", docs_url=None, redoc_url=None)

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
    "average_level": 0,
    "average_session": 0,
    "weekly_passes": 0,
    "evals_completed": 0,
    "zombies": [],
    "active_projects": [],
    "top_evaluators": [],
    "weekly_logtime": [],
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
