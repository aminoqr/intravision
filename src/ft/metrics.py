"""Raw API payloads -> the processed metrics the dashboard renders.

Pure functions, no network, no database. Everything here is unit-testable against
fixtures, which is what makes it safe to iterate on at 3am without spending quota.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

Json = dict[str, Any]


def parse_dt(value: str | None) -> datetime | None:
    """Parses 42's timestamps. They arrive as both '...Z' and '...+01:00'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _user_card(user: Json | None) -> Json:
    user = user or {}
    return {
        "login": user.get("login", "unknown"),
        "name": user.get("displayname") or user.get("login", "unknown"),
        "image": (user.get("image") or {}).get("link") or user.get("image_url"),
    }


def is_validated(pu: Json) -> bool:
    """`validated?` keeps its Ruby question mark in the JSON. Both spellings appear."""
    flag = pu.get("validated?")
    if flag is None:
        flag = pu.get("validated")
    return bool(flag)


def recent_validations(
    projects_users: Iterable[Json],
    limit: int = 12,
    now: datetime | None = None,
) -> list[Json]:
    """The hero panel: who validated what, most recent first."""
    now = now or datetime.now(timezone.utc)
    rows = []

    for pu in projects_users:
        if not is_validated(pu):
            continue
        marked = parse_dt(pu.get("marked_at"))
        if marked is None:
            continue
        project = pu.get("project") or {}
        rows.append(
            {
                "user": _user_card(pu.get("user")),
                "project": project.get("name") or project.get("slug") or "?",
                "mark": pu.get("final_mark"),
                # occurrence is 0-indexed; attempt 3 is a persistence story worth showing.
                "attempt": (pu.get("occurrence") or 0) + 1,
                "marked_at": marked.isoformat(),
                "age_seconds": max(0, int((now - marked).total_seconds())),
            }
        )

    rows.sort(key=lambda r: r["marked_at"], reverse=True)
    return rows[:limit]


def project_popularity(projects_users: Iterable[Json], limit: int = 8) -> list[Json]:
    """What campus is working on right now. Helps people find peers at the same stage."""
    counts: Counter[str] = Counter()
    for pu in projects_users:
        if pu.get("status") != "in_progress":
            continue
        project = pu.get("project") or {}
        name = project.get("name") or project.get("slug")
        if name:
            counts[name] += 1
    return [{"project": name, "count": n} for name, n in counts.most_common(limit)]


def level_distribution(cursus_users: Iterable[Json], bucket: float = 1.0) -> list[Json]:
    """Histogram of levels. A shape, not a ranking - nobody is named."""
    buckets: Counter[int] = Counter()
    levels: list[float] = []

    for cu in cursus_users:
        if cu.get("blackholed_at"):
            continue
        level = cu.get("level")
        if level is None:
            continue
        levels.append(float(level))
        buckets[int(float(level) // bucket)] += 1

    if not levels:
        return []

    top = max(buckets)
    return [
        {
            "label": f"{int(i * bucket)}-{int((i + 1) * bucket)}",
            "count": buckets.get(i, 0),
        }
        for i in range(top + 1)
    ]


def median_level(cursus_users: Iterable[Json]) -> float:
    levels = sorted(
        float(cu["level"])
        for cu in cursus_users
        if cu.get("level") is not None and not cu.get("blackholed_at")
    )
    if not levels:
        return 0.0
    mid = len(levels) // 2
    if len(levels) % 2:
        return round(levels[mid], 2)
    return round((levels[mid - 1] + levels[mid]) / 2, 2)


def coalition_standings(coalitions: Iterable[Json]) -> list[Json]:
    """Ranking where being low is fine, because it's teams rather than individuals."""
    rows = [
        {
            "name": c.get("name", "?"),
            "score": c.get("score") or 0,
            "color": c.get("color") or "#888888",
            "image": c.get("image_url"),
        }
        for c in coalitions
    ]
    rows.sort(key=lambda r: r["score"], reverse=True)

    top = max((r["score"] for r in rows), default=0)
    for row in rows:
        row["pct"] = round(100 * row["score"] / top, 1) if top else 0.0
    return rows


def active_now(locations: Iterable[Json]) -> int:
    """Locations with no end_at are open sessions - people currently on campus.

    Drifts high when students forget to log out. Present as approximate.
    """
    return sum(1 for loc in locations if not loc.get("end_at"))


def level_ups(
    current: Iterable[Json],
    previous: Iterable[Json] | None,
    threshold: float = 1.0,
) -> list[Json]:
    """Students who crossed a whole level since the previous snapshot."""
    if not previous:
        return []

    before: dict[int, float] = {}
    for cu in previous:
        user_id = (cu.get("user") or {}).get("id")
        if user_id is not None and cu.get("level") is not None:
            before[user_id] = float(cu["level"])

    risen = []
    for cu in current:
        user = cu.get("user") or {}
        user_id = user.get("id")
        level = cu.get("level")
        if user_id is None or level is None or user_id not in before:
            continue
        old, new = before[user_id], float(level)
        if int(new // threshold) > int(old // threshold):
            risen.append(
                {
                    "user": _user_card(user),
                    "from": round(old, 2),
                    "to": round(new, 2),
                    "milestone": int(new // threshold * threshold),
                }
            )

    risen.sort(key=lambda r: r["to"], reverse=True)
    return risen


def validations_since(
    projects_users: Iterable[Json],
    days: int = 7,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    total = 0
    for pu in projects_users:
        if not is_validated(pu):
            continue
        marked = parse_dt(pu.get("marked_at"))
        if marked and marked >= cutoff:
            total += 1
    return total


def build_metrics(
    *,
    projects_users: list[Json],
    cursus_users: list[Json],
    coalitions: list[Json],
    locations: list[Json],
    previous_cursus_users: list[Json] | None = None,
    now: datetime | None = None,
) -> Json:
    """Assembles everything the renderer needs into one JSON blob."""
    now = now or datetime.now(timezone.utc)
    active_students = [cu for cu in cursus_users if not cu.get("blackholed_at")]

    return {
        "generated_at": now.isoformat(),
        "pulse": {
            "on_campus": active_now(locations),
            "validated_this_week": validations_since(projects_users, days=7, now=now),
            "active_students": len(active_students),
            "median_level": median_level(cursus_users),
        },
        "recent_validations": recent_validations(projects_users, limit=12, now=now),
        "project_popularity": project_popularity(projects_users),
        "level_distribution": level_distribution(cursus_users),
        "coalitions": coalition_standings(coalitions),
        "level_ups": level_ups(cursus_users, previous_cursus_users),
    }
