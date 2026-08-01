"""Raw API payloads -> the processed metrics the dashboard renders.

Pure functions, no network, no database. Everything here is unit-testable against
fixtures, which is what makes it safe to iterate on at 3am without spending quota.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
import re

Json = dict[str, Any]


def parse_dt(value: str | None) -> datetime | None:
    """Parses 42's timestamps. They arrive as both '...Z' and '...+01:00'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_blackholed(cu: Json, now: datetime | None = None) -> bool:
    """True only when blackholed_at is in the past.

    Intra sets blackholed_at to the *deadline* while a Cadet is still safe.
    A future date means they are still active — only a past date is absorbed.
    """
    now = now or datetime.now(timezone.utc)
    deadline = parse_dt(cu.get("blackholed_at"))
    if deadline is None:
        return False
    return deadline <= now


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


def active_on_campus(locations: Iterable[Json]) -> list[Json]:
    """Every cadet currently logged into a campus host (from locations).

    No length cap — the TV carousel loops the full list when it overflows.
    """
    seen: set[str] = set()
    rows: list[Json] = []
    for loc in locations:
        if loc.get("end_at"):
            continue
        user = loc.get("user") or {}
        login = user.get("login")
        if not login or login in seen:
            continue
        seen.add(login)
        card = _user_card(user)
        card["host"] = loc.get("host") or ""
        rows.append(card)
    return rows


WARSAW_CLUSTER_SEATS = {"C1": 52, "C2": 36, "C3": 64}


def cluster_capacity(locations: Iterable[Json]) -> list[Json]:
    """Occupied seats vs physical station count per cluster (C1/C2/C3).

    Parses the ``host`` field (e.g. ``c1r8s4``) — the first two characters
    tell which cluster the student is sitting in right now.

    Max stations are the real 42 Warsaw campus counts:
    C1 = 52, C2 = 36, C3 = 64, Total = 152.
    """
    occupied: dict[str, int] = {"C1": 0, "C2": 0, "C3": 0}
    for loc in locations:
        if loc.get("end_at"):
            continue
        host = (loc.get("host") or "").lower()
        match = re.match(r"^c([123])", host)
        if match:
            occupied[f"C{match.group(1)}"] += 1

    occupied_total = sum(occupied.values())
    max_total = sum(WARSAW_CLUSTER_SEATS.values())

    return [
        {"key": "Total", "value": occupied_total, "max": max_total, "color": "#0A84FF"},
        {"key": "C1", "value": occupied["C1"], "max": WARSAW_CLUSTER_SEATS["C1"], "color": "#FFD60A"},
        {"key": "C2", "value": occupied["C2"], "max": WARSAW_CLUSTER_SEATS["C2"], "color": "#30D158"},
        {"key": "C3", "value": occupied["C3"], "max": WARSAW_CLUSTER_SEATS["C3"], "color": "#FF453A"},
    ]


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


def avg_milestone(
    cursus_users: Iterable[Json],
    now: datetime | None = None,
) -> float:
    """Mean whole-level milestone across students not yet blackholed.

    The Intra API has no separate milestone field. Common Core progress is
    tracked via cursus `level`; whole-level crossings are the milestones we
    already surface in level_ups (e.g. level 3.8 → milestone 3).
    """
    now = now or datetime.now(timezone.utc)
    milestones = [
        int(float(cu["level"]))
        for cu in cursus_users
        if cu.get("level") is not None and not is_blackholed(cu, now=now)
    ]
    if not milestones:
        return 0.0
    return round(sum(milestones) / len(milestones), 1)


def avg_session_hours(
    locations: Iterable[Json], now: datetime | None = None
) -> float:
    """Mean session duration in hours.

    Active sessions (no end_at) measure begin_at → now.
    """
    now = now or datetime.now(timezone.utc)
    durations: list[float] = []
    for loc in locations:
        begin = parse_dt(loc.get("begin_at"))
        if begin is None:
            continue
        end = parse_dt(loc.get("end_at")) or now
        hours = max(0.0, (end - begin).total_seconds()) / 3600
        durations.append(hours)
    if not durations:
        return 0.0
    return round(sum(durations) / len(durations), 1)


def weekly_pass_count(
    projects_users: Iterable[Json],
    days: int = 7,
    now: datetime | None = None,
) -> int:
    """Count finished projects with mark >= 75 in the last N days."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    count = 0
    for pu in projects_users:
        if pu.get("status") != "finished":
            continue
        if (pu.get("final_mark") or 0) < 75:
            continue
        marked = parse_dt(pu.get("marked_at"))
        if marked and marked >= cutoff:
            count += 1
    return count


def evals_completed_count(
    projects_users: Iterable[Json],
    days: int = 7,
    now: datetime | None = None,
) -> int:
    """Peer evaluation count approximated from validated projects.

    Without scale_teams (needs elevated access or returns 422 via /graph),
    each validated project implies at least one completed peer review.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    count = 0
    for pu in projects_users:
        if not is_validated(pu):
            continue
        marked = parse_dt(pu.get("marked_at"))
        if marked and marked >= cutoff:
            count += 1
    return count


def sleepless_zombies(
    locations: Iterable[Json],
    days: int = 7,
    limit: int = 5,
    now: datetime | None = None,
) -> list[Json]:
    """Top N users by total campus hours in the last N days."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    hours_by_user: dict[str, float] = defaultdict(float)
    user_cards: dict[str, Json] = {}

    for loc in locations:
        begin = parse_dt(loc.get("begin_at"))
        if begin is None:
            continue
        end = parse_dt(loc.get("end_at")) or now
        if end < cutoff:
            continue
        effective_begin = max(begin, cutoff)
        hours = max(0.0, (end - effective_begin).total_seconds()) / 3600

        user = loc.get("user") or {}
        login = user.get("login", "unknown")
        hours_by_user[login] += hours
        if login not in user_cards:
            user_cards[login] = _user_card(user)

    ranked = sorted(hours_by_user.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [
        {"user": user_cards[login], "hours": round(h, 1), "rank": i + 1}
        for i, (login, h) in enumerate(ranked)
    ]


DONUT_PALETTE = [
    "#6366f1",  # indigo
    "#ec4899",  # pink
    "#ef4444",  # red
    "#f59e0b",  # amber
    "#f97316",  # orange
    "#8b5cf6",  # violet
    "#06b6d4",  # cyan
    "#64748b",  # slate — reserved for "Others"
]


def active_project_data(projects_users: Iterable[Json]) -> list[Json]:
    """Truthful donut nodes for in-progress projects.

    Rules:
    1. Count each project name with Counter among status == "in_progress".
    2. Sort descending by active count.
    3. If more than 7 unique projects: keep top 7, fold the rest into "Others".
    4. Percentage of each final node is (count / Total) * 100, one decimal, with "%".
    """
    counts: Counter[str] = Counter()
    for pu in projects_users:
        if pu.get("status") != "in_progress":
            continue
        project = pu.get("project") or {}
        name = project.get("name") or project.get("slug")
        if name:
            counts[name] += 1

    total = sum(counts.values())
    if not total:
        return []

    ranked = counts.most_common()
    if len(ranked) > 7:
        head = ranked[:7]
        others_count = sum(n for _, n in ranked[7:])
        slices: list[tuple[str, int]] = list(head) + [("Others", others_count)]
    else:
        slices = list(ranked)

    nodes: list[Json] = []
    for i, (name, n) in enumerate(slices):
        percentage = f"{(n / total) * 100:.1f}%"
        color = DONUT_PALETTE[i % len(DONUT_PALETTE)]
        if name == "Others":
            color = DONUT_PALETTE[-1]
        nodes.append(
            {
                "name": name,
                "count": n,
                "percentage": percentage,
                "color": color,
            }
        )
    return nodes


def top_evaluators(
    projects_users: Iterable[Json],
    days: int = 14,
    limit: int = 4,
    now: datetime | None = None,
) -> list[Json]:
    """Top N users by validated project count (proxy for evaluations given).

    Without direct scale_teams access, the best available signal for
    "who contributes most evaluations" is how many projects they completed.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    eval_counts: Counter[str] = Counter()
    user_cards: dict[str, Json] = {}

    for pu in projects_users:
        if not is_validated(pu):
            continue
        marked = parse_dt(pu.get("marked_at"))
        if not marked or marked < cutoff:
            continue
        user = pu.get("user") or {}
        login = user.get("login", "unknown")
        eval_counts[login] += 1
        if login not in user_cards:
            user_cards[login] = _user_card(user)

    return [
        {"user": user_cards[login], "count": n}
        for login, n in eval_counts.most_common(limit)
    ]


def weekly_logtime(
    locations: Iterable[Json],
    days: int = 7,
    now: datetime | None = None,
) -> list[Json]:
    """Cumulative campus hours by day of week for a bar chart.

    Long sessions are split at midnight boundaries so each day gets
    its correct share of the hours.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    hours_by_day: dict[int, float] = defaultdict(float)

    for loc in locations:
        begin = parse_dt(loc.get("begin_at"))
        if begin is None:
            continue
        end = parse_dt(loc.get("end_at")) or now
        if end < cutoff:
            continue

        cursor = max(begin, cutoff)
        while cursor < end:
            next_midnight = (cursor + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            segment_end = min(end, next_midnight)
            hours = max(0.0, (segment_end - cursor).total_seconds()) / 3600
            hours_by_day[cursor.weekday()] += hours
            cursor = segment_end

    return [
        {"day": day_names[i], "hours": round(hours_by_day.get(i, 0), 1)}
        for i in range(7)
    ]


def build_metrics(
    *,
    projects_users: list[Json],
    cursus_users: list[Json],
    coalitions: list[Json],
    locations: list[Json],
    locations_recent: list[Json] | None = None,
    previous_cursus_users: list[Json] | None = None,
    now: datetime | None = None,
) -> Json:
    """Assembles everything the renderer needs into one JSON blob."""
    now = now or datetime.now(timezone.utc)
    active_students = [cu for cu in cursus_users if not cu.get("blackholed_at")]
    # 7-day location history powers weekly charts; fall back to live locations.
    location_history = locations_recent if locations_recent else locations

    return {
        "generated_at": now.isoformat(),
        # Page 1
        "pulse": {
            "on_campus": active_now(locations),
            "validated_this_week": validations_since(projects_users, days=7, now=now),
            "active_students": len(active_students),
            "median_level": median_level(cursus_users),
        },
        "active_on_campus": active_on_campus(locations),
        "cluster_capacity": cluster_capacity(locations),
        "recent_validations": recent_validations(projects_users, limit=12, now=now),
        "project_popularity": project_popularity(projects_users),
        "level_distribution": level_distribution(cursus_users),
        "coalitions": coalition_standings(coalitions),
        "level_ups": level_ups(cursus_users, previous_cursus_users),
        # Page 2 — Hall of Fame & Telemetry
        "average_milestone": avg_milestone(cursus_users, now=now),
        "average_session": avg_session_hours(locations, now=now),
        "weekly_passes": weekly_pass_count(projects_users, now=now),
        "evals_completed": evals_completed_count(projects_users, now=now),
        "zombies": sleepless_zombies(location_history, now=now),
        # Page 3 — Projects & Peer Analytics
        "active_project_data": active_project_data(projects_users),
        "top_evaluators": top_evaluators(projects_users, now=now),
        "weekly_logtime": weekly_logtime(locations, now=now),
    }
