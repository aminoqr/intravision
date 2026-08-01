"""Probes the 42 API and writes a findings report.

This exists because the API research deliverable is graded on understanding, and
measured facts ("we called it, here is what came back") beat paraphrasing the
public docs. Run it once before the hackathon, paste the output into the doc.

Budget: ~25 requests. Safe to run a few times per hour.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .client import FtClient
from .config import ROOT, Config

log = logging.getLogger("ft.probe")

Json = dict[str, Any]


class Probe:
    def __init__(self, client: FtClient):
        self.client = client
        self.results: list[Json] = []

    def check(self, label: str, path: str, params: Json | None = None) -> Json | None:
        entry: Json = {"label": label, "path": path, "params": params or {}}
        try:
            resp = self.client.get(path, params)
            body = resp.json()
            entry.update(
                status=resp.status_code,
                ok=True,
                count=len(body) if isinstance(body, list) else None,
                total=resp.headers.get("X-Total"),
                per_page=resp.headers.get("X-Per-Page"),
                roles=resp.headers.get("X-Application-Roles"),
                sample=self._sample(body),
            )
            log.info("OK   %s", label)
            return body
        except Exception as exc:  # noqa: BLE001 - probing is expected to fail sometimes
            entry.update(status=getattr(exc, "response", None) and exc.response.status_code,
                         ok=False, error=str(exc)[:200])
            log.warning("FAIL %s: %s", label, str(exc)[:120])
            return None
        finally:
            self.results.append(entry)

    @staticmethod
    def _sample(body: Any) -> Any:
        """First element with keys only - enough to learn the shape, short enough to read."""
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, dict):
                return {"keys": sorted(first.keys())}
            return first
        if isinstance(body, dict):
            return {"keys": sorted(body.keys())}
        return body


def run(cfg: Config) -> list[Json]:
    today = datetime.now(timezone.utc).date()
    week_ago = today - timedelta(days=7)

    with FtClient(cfg.uid, cfg.secret, base_url=cfg.base_url) as client:
        p = Probe(client)

        # --- identity and access -------------------------------------------
        p.check("token scopes and app roles", "/oauth/token/info")

        # --- id resolution --------------------------------------------------
        campuses = p.check("campus lookup by name", "/v2/campus", {"filter[name]": "Warsaw"})
        if campuses:
            log.info("Warsaw campus ids: %s", [c.get("id") for c in campuses])
        p.check("cursus list", "/v2/cursus", {"page[size]": 100})

        # --- pagination behaviour -------------------------------------------
        p.check(
            "page[size]=100 honoured?",
            f"/v2/cursus/{cfg.cursus_id}/cursus_users",
            {"page[size]": 100, "filter[campus_id]": cfg.campus_id},
        )

        # --- filter support (undocumented in the guides) ---------------------
        p.check(
            "filter[campus_id] on cursus_users",
            "/v2/cursus_users",
            {"filter[campus_id]": cfg.campus_id, "page[size]": 5},
        )
        p.check(
            "filter[campus] on projects_users",
            "/v2/projects_users",
            {"filter[campus]": cfg.campus_id, "page[size]": 5},
        )
        p.check(
            "sort=-marked_at on projects_users",
            "/v2/projects_users",
            {"sort": "-marked_at", "page[size]": 5},
        )
        p.check(
            "range[marked_at] on projects_users",
            "/v2/projects_users",
            {"range[marked_at]": f"{week_ago},{today}", "page[size]": 5},
        )

        # --- the panels' data sources ---------------------------------------
        p.check("campus stats", f"/v2/campus/{cfg.campus_id}/stats")
        p.check(
            "active locations",
            f"/v2/campus/{cfg.campus_id}/locations",
            {"filter[active]": "true", "page[size]": 5},
        )
        p.check("blocs for campus", "/v2/blocs", {"filter[campus_id]": cfg.campus_id})
        p.check("coalitions", "/v2/coalitions", {"page[size]": 5})
        p.check("achievements (may need elevated access)", "/v2/achievements", {"page[size]": 5})

        # --- graph endpoints: the rate-limit escape hatch ---------------------
        for path in (
            "/v2/projects_users/graph/on/marked_at/by/day",
            "/v2/cursus_users/graph/on/level/by/day",
            "/v2/locations/graph/on/begin_at/by/day",
            "/v2/scale_teams/graph/on/created_at/by/day",
        ):
            p.check(f"graph: {path.split('/v2/')[1]}", path)

        log.info("probe used %d requests", client.limiter.total_requests)
        return p.results


def to_markdown(results: list[Json]) -> str:
    lines = [
        "# 42 API probe results",
        "",
        f"Run: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Measured behaviour, not documentation. Anything marked FAIL either needs elevated",
        "access or does not support the parameter tested - both are findings worth reporting.",
        "",
        "| Check | Endpoint | Status | Rows | X-Total |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        mark = "OK" if r.get("ok") else "FAIL"
        lines.append(
            f"| {r['label']} | `{r['path']}` | {mark} {r.get('status') or ''} | "
            f"{r.get('count') if r.get('count') is not None else '-'} | {r.get('total') or '-'} |"
        )

    lines += ["", "## Response shapes", ""]
    for r in results:
        if r.get("ok") and r.get("sample"):
            lines += [f"### {r['label']}", "", "```json", json.dumps(r["sample"], indent=2), "```", ""]

    failed = [r for r in results if not r.get("ok")]
    if failed:
        lines += ["## Failures", ""]
        for r in failed:
            lines.append(f"- **{r['label']}** (`{r['path']}`): {r.get('error', 'unknown')}")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe the 42 API and write findings")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=ROOT / "docs" / "api-probe-results.md",
        help="where to write the markdown report",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    results = run(Config.from_env())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(to_markdown(results))

    ok = sum(1 for r in results if r.get("ok"))
    print(f"\n{ok}/{len(results)} checks passed -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
