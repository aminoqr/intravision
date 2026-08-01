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

    def projects_users() -> list[Json]:
        # range[marked_at] keeps this bounded; projects_users is a huge collection.
        return list(
            client.paginate(
                "/v2/projects_users",
                params={
                    "filter[campus]": campus,
                    "range[marked_at]": f"{since},{datetime.now(timezone.utc).date().isoformat()}",
                    "sort": "-marked_at",
                },
                max_pages=cfg.max_pages,
            )
        )

    def cursus_users() -> list[Json]:
        return list(
            client.paginate(
                f"/v2/cursus/{cfg.cursus_id}/cursus_users",
                params={"filter[campus_id]": campus},
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
                params={"filter[active]": "true"},
                max_pages=5,
            )
        )

    def locations_recent() -> list[Json]:
        """All sessions in the past 7 days — both active and ended.

        Unique hosts from this set give us the real station count per cluster
        without guessing or hardcoding seat numbers.
        """
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        return list(
            client.paginate(
                f"/v2/campus/{campus}/locations",
                params={
                    "range[begin_at]": f"{week_ago},{datetime.now(timezone.utc).isoformat()}",
                    "sort": "-begin_at",
                },
                max_pages=10,
            )
        )

    return {
        "projects_users": _safe("projects_users", projects_users),
        "cursus_users": _safe("cursus_users", cursus_users),
        "coalitions": _safe("coalitions", coalitions),
        "locations": _safe("locations_active", locations_active),
        "locations_recent": _safe("locations_recent", locations_recent),
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
    for name in ("projects_users", "cursus_users", "coalitions", "locations", "locations_recent"):
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
        previous_cursus_users=previous,
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
