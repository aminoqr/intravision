"""The fetch job: 42 API -> processed metrics -> store.

Runs on a schedule or on demand. Never called from a request path.

Every endpoint is fetched defensively: if one fails, the others still land and the
dashboard degrades to stale data for that panel instead of going blank.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .client import FtClient
from .config import FIXTURES_DIR, Config
from .metrics import build_metrics
from .store import Store, utcnow

log = logging.getLogger("ft.fetch")

Json = dict[str, Any]


def _safe(label: str, fn: Callable[[], list[Json]]) -> list[Json]:
    try:
        rows = fn()
        log.info("%s: %d rows", label, len(rows))
        return rows
    except Exception as exc:  # noqa: BLE001 - one bad endpoint must not kill the refresh
        log.error("%s failed: %s", label, exc)
        return []


def fetch_raw(client: FtClient, cfg: Config) -> dict[str, list[Json]]:
    campus = cfg.campus_id
    since = (datetime.now(timezone.utc) - timedelta(days=14)).date().isoformat()
    today = datetime.now(timezone.utc).date().isoformat()

    def _merge_by_id(*batches: list[Json]) -> list[Json]:
        """Union rows from multiple projects_users queries, keyed by id."""
        by_id: dict[Any, Json] = {}
        for batch in batches:
            for row in batch:
                row_id = row.get("id")
                if row_id is None:
                    continue
                by_id[row_id] = row
        return list(by_id.values())

    def projects_users() -> list[Json]:
        """Campus-wide projects_users with no artificial row cap.

        Page 3 active-project spectrum must see EVERY in_progress row on
        campus — not just the ~12 that happen to fall inside a 14-day
        marked_at window.

        Intra filter note (see docs/42-api.md + probe results):
          /v2/projects_users uses filter[campus]=<id>  (NOT filter[campus_id]).
          Equivalent target URL shape:
            /v2/projects_users?filter[campus]=67&filter[status]=in_progress&page[size]=100

        There is no SQL projects_users table and no LIMIT 12 anywhere in the
        store path — metrics are built in Python from this full JSON payload
        with `status == "in_progress"` and zero row caps on that filter.
        """
        # 1) Recently marked — feeds fame / validations / weekly pass panels.
        recent = list(
            client.paginate(
                "/v2/projects_users",
                params={
                    "filter[campus]": campus,
                    "range[marked_at]": f"{since},{today}",
                    "sort": "-marked_at",
                    "page[size]": 100,
                },
                page_size=100,
                max_pages=cfg.max_pages,
            )
        )
        # 2) All in_progress on campus — feeds Page 3 active-project spectrum.
        in_progress = list(
            client.paginate(
                "/v2/projects_users",
                params={
                    "filter[campus]": campus,
                    "filter[status]": "in_progress",
                    "page[size]": 100,
                },
                page_size=100,
                max_pages=cfg.max_pages,
            )
        )
        merged = _merge_by_id(recent, in_progress)
        log.info(
            "projects_users merge: recent=%d in_progress=%d unique=%d (no LIMIT)",
            len(recent),
            len(in_progress),
            len(merged),
        )
        return merged

    def cursus_users() -> list[Json]:
        return list(
            client.paginate(
                f"/v2/cursus/{cfg.cursus_id}/cursus_users",
                params={"filter[campus_id]": campus, "page[size]": 100},
                page_size=100,
                max_pages=cfg.max_pages,
            )
        )

    def coalitions() -> list[Json]:
        blocs = client.get_json("/v2/blocs", params={"filter[campus_id]": campus})
        rows: list[Json] = []
        for bloc in blocs if isinstance(blocs, list) else []:
            rows.extend(bloc.get("coalitions") or [])
        if not rows:
            rows = list(client.paginate("/v2/coalitions", max_pages=2))
        return rows

    def locations_active() -> list[Json]:
        return list(
            client.paginate(
                f"/v2/campus/{campus}/locations",
                params={"filter[active]": "true", "page[size]": 100},
                page_size=100,
                max_pages=5,
            )
        )

    def locations_recent() -> list[Json]:
        """Campus location history for the rolling absolute past 7 days.

        Target shape (dates computed dynamically via datetime.now(UTC)):
          GET /v2/campus/{id}/locations
            ?range[begin_at]=<UTC-midnight-7d>,<now>
            &sort=-begin_at
            &page[size]=100

        Feeds Page 3 weekly_logtime_data and Page 2 sleepless zombies.
        Active-only locations are NOT enough — they collapse the chart to
        "today only" (e.g. Saturday-only bars).
        """
        now_utc = datetime.now(timezone.utc)
        window_start = (now_utc - timedelta(days=7)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return list(
            client.paginate(
                f"/v2/campus/{campus}/locations",
                params={
                    "range[begin_at]": (
                        f"{window_start.strftime('%Y-%m-%dT%H:%M:%S.000Z')},"
                        f"{now_utc.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z"
                    ),
                    "sort": "-begin_at",
                    "page[size]": 100,
                },
                page_size=100,
                max_pages=20,
            )
        )

    def scale_teams() -> list[Json]:
        """Filled peer evaluations (corrector → evaluatee) when the app role allows.

        Soft-fails via `_safe` on many unprivileged apps (400/timeout). Empty
        list triggers the metrics fallback that still credits evaluations given
        to OTHER cadets (never self).
        """
        since = (datetime.now(timezone.utc) - timedelta(days=45)).date().isoformat()
        return list(
            client.paginate(
                "/v2/scale_teams",
                params={
                    "filter[campus_id]": campus,
                    "filter[filled]": "true",
                    "range[filled_at]": f"{since},{today}",
                    "page[size]": 100,
                },
                page_size=100,
                max_pages=min(cfg.max_pages, 10),
            )
        )

    return {
        "projects_users": _safe("projects_users", projects_users),
        "cursus_users": _safe("cursus_users", cursus_users),
        "coalitions": _safe("coalitions", coalitions),
        "locations": _safe("locations_active", locations_active),
        "locations_recent": _safe("locations_recent", locations_recent),
        "scale_teams": _safe("scale_teams", scale_teams),
    }


def dump_fixtures(raw: dict[str, list[Json]], directory: Path = FIXTURES_DIR) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, rows in raw.items():
        (directory / f"{name}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2)
        )
    log.info("fixtures written to %s", directory)


def load_fixtures(directory: Path = FIXTURES_DIR) -> dict[str, list[Json]]:
    raw: dict[str, list[Json]] = {}
    for name in (
        "projects_users",
        "cursus_users",
        "coalitions",
        "locations",
        "locations_recent",
        "scale_teams",
    ):
        path = directory / f"{name}.json"
        raw[name] = json.loads(path.read_text()) if path.exists() else []
    return raw


def refresh(cfg: Config, use_fixtures: bool = False, save_fixtures: bool = False) -> Json:
    store = Store(cfg.db_path)
    requests_used = 0

    if use_fixtures:
        log.info("using fixtures (no API calls)")
        raw = load_fixtures()
        source = "fixtures"
    else:
        with FtClient(cfg.uid, cfg.secret, base_url=cfg.base_url) as client:
            raw = fetch_raw(client, cfg)
            requests_used = client.limiter.total_requests
            log.info("used %d requests this refresh", requests_used)
        source = "api"
        if save_fixtures:
            dump_fixtures(raw)

    # Diff against the previous snapshot to detect level-ups.
    previous = store.previous("cursus_users", before=utcnow())

    metrics = build_metrics(
        projects_users=raw["projects_users"],
        cursus_users=raw["cursus_users"],
        coalitions=raw["coalitions"],
        locations=raw["locations"],
        locations_recent=raw.get("locations_recent", []),
        scale_teams=raw.get("scale_teams", []),
        previous_cursus_users=previous,
        cursus_id=cfg.cursus_id,
    )
    metrics["source"] = source
    metrics["requests_used"] = requests_used

    store.put("cursus_users", raw["cursus_users"], keep_history=True)
    store.prune_history("cursus_users", keep=48)
    store.put("metrics", metrics)
    store.set_meta("last_refresh", utcnow())
    store.set_meta("last_refresh_source", source)
    store.close()

    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch 42 data and rebuild metrics")
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="build from fixtures/ instead of the API (costs no quota)",
    )
    parser.add_argument(
        "--save-fixtures",
        action="store_true",
        help="write the raw API responses to fixtures/ for offline development",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    cfg = Config.from_env()
    metrics = refresh(cfg, use_fixtures=args.fixtures, save_fixtures=args.save_fixtures)

    pulse = metrics["pulse"]
    print(
        f"ok  on_campus={pulse['on_campus']}  "
        f"validated_7d={pulse['validated_this_week']}  "
        f"students={pulse['active_students']}  "
        f"recent={len(metrics['recent_validations'])}  "
        f"requests={metrics['requests_used']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
