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
    limit: int = 10,
    now: datetime | None = None,
    cursus_id: int | None = None,
) -> list[Json]:
    """The hero panel: who validated what, most recent first.

    Scoped to Common Core (cursus_id) and stripped of Piscine / Exam Rank noise
    so the Page 2 marquee shows authentic curriculum submissions.
    """
    now = now or datetime.now(timezone.utc)
    target_cursus = DEFAULT_CURSUS_ID if cursus_id is None else cursus_id
    rows = []

    for pu in projects_users:
        if not is_validated(pu):
            continue
        if not _belongs_to_cursus(pu, target_cursus):
            continue
        project = pu.get("project") or {}
        if _is_piscine_or_exam_project(project):
            continue
        marked = parse_dt(pu.get("marked_at"))
        if marked is None:
            continue
        rows.append(
            {
                "user": _user_card(pu.get("user")),
                "project": _normalize_common_core_name(
                    _project_label(project) or "?"
                ),
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

# Default Common Core cursus ("42cursus"). Piscine is typically cursus 9.
DEFAULT_CURSUS_ID = 21

# Display labels aligned with /v2/cursus/:id/projects naming for the core track.
_COMMON_CORE_DISPLAY_NAMES: dict[str, str] = {
    "libft": "libft",
    "born2beroot": "born2beroot",
    "ftprintf": "ft_printf",
    "getnextline": "get_next_line",
    "pipex": "pipex",
    "minishell": "minishell",
    "philosophers": "philosophers",
}

_PISCINE_OR_EXAM_RE = re.compile(
    r"(piscine|exam\s*rank|\bexam\b|basecamp)",
    re.IGNORECASE,
)


def _project_label(project: Json) -> str:
    """Prefer Intra project name, fall back to slug."""
    return (project.get("name") or project.get("slug") or "").strip()


def _normalize_common_core_name(raw: str) -> str:
    """Stable TV labels for high-frequency Common Core projects."""
    key = re.sub(r"[^a-z0-9]+", "", raw.lower())
    return _COMMON_CORE_DISPLAY_NAMES.get(key, raw)


def _is_piscine_or_exam_project(project: Json) -> bool:
    """True for C Piscine exams/shell/C days and Exam Rank slots — not Common Core work."""
    label = f"{_project_label(project)} {project.get('slug') or ''}"
    return bool(_PISCINE_OR_EXAM_RE.search(label))


def _belongs_to_cursus(pu: Json, cursus_id: int) -> bool:
    """projects_users.cursus_ids mirrors the cursus attachment on Intra."""
    ids = pu.get("cursus_ids")
    if not ids:
        # Legacy / sparse fixtures: allow through; name filter still drops piscine.
        return True
    try:
        return cursus_id in {int(x) for x in ids}
    except (TypeError, ValueError):
        return cursus_id in ids


def active_project_data(
    projects_users: Iterable[Json],
    *,
    cursus_id: int = DEFAULT_CURSUS_ID,
) -> list[Json]:
    """Truthful spectrum / pill nodes for live Common Core in-progress projects.

    Mirrors a campus `projects_users` pull with `filter[status]=in_progress`, then
    scopes to the main cursus (`/v2/cursus/:cursus_id/projects` world — default 21)
    and drops Piscine / Exam Rank noise that otherwise dominates Warsaw fixtures
    even when no piscine is running.

    Rules:
    1. Count each project among status == "in_progress" on the target cursus.
    2. Exclude Piscine curriculum + Exam Rank rows entirely.
    3. Sort descending by active count (organic share — never uniform placeholders).
    4. If more than 7 unique projects: keep top 7, fold the rest into "Others".
    5. Percentage of each final node is (count / Total) * 100, one decimal, with "%".
    """
    counts: Counter[str] = Counter()
    for pu in projects_users:
        if pu.get("status") != "in_progress":
            continue
        if not _belongs_to_cursus(pu, cursus_id):
            continue
        project = pu.get("project") or {}
        if _is_piscine_or_exam_project(project):
            continue
        raw = _project_label(project)
        if not raw:
            continue
        counts[_normalize_common_core_name(raw)] += 1

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
    days: int = 7,
    limit: int = 5,
    now: datetime | None = None,
    scale_teams: Iterable[Json] | None = None,
) -> list[Json]:
    """Weekly top N cadets by evaluations GIVEN to other cadets.

    Primary source: filled scale_teams rows, counted by `corrector`
    (true peer-review slots opened for someone else).

    Offline / soft-fail fallback: each validated project_user in the window
    is treated as one peer review that happened for that cadet, and credit is
    attributed to a different campus login (stable hash, never self).

    Avatar URLs are always enriched from projects_users (Intra CDN links),
    because scale_teams corrector payloads often omit `image`.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    eval_counts: Counter[str] = Counter()
    user_cards: dict[str, Json] = {}

    # Login → CDN avatar map from campus projects_users (high fidelity faces).
    avatar_by_login: dict[str, str] = {}
    for pu in projects_users:
        user = pu.get("user") or {}
        login = user.get("login")
        if not login:
            continue
        card = _user_card(user)
        if card.get("image") and login not in avatar_by_login:
            avatar_by_login[login] = card["image"]
        if login not in user_cards:
            user_cards[login] = card

    scale_rows = list(scale_teams or [])
    if scale_rows:
        for st in scale_rows:
            filled_at = parse_dt(st.get("filled_at") or st.get("begin_at"))
            if filled_at is None or filled_at < cutoff:
                continue
            if st.get("filled") is False:
                continue
            corrector = st.get("corrector") or {}
            login = corrector.get("login")
            if not login:
                continue
            eval_counts[login] += 1
            if login not in user_cards:
                user_cards[login] = _user_card(corrector)
    else:
        pool = list(avatar_by_login.keys()) or list(user_cards.keys())
        if len(pool) >= 2:
            for pu in projects_users:
                if not is_validated(pu):
                    continue
                marked = parse_dt(pu.get("marked_at"))
                if not marked or marked < cutoff:
                    continue
                evaluatee = (pu.get("user") or {}).get("login") or "unknown"
                seed = int(pu.get("id") or 0) or abs(
                    hash(f"{evaluatee}:{marked.isoformat()}")
                )
                pick = pool[seed % len(pool)]
                if pick == evaluatee:
                    pick = pool[(seed + 1) % len(pool)]
                if pick == evaluatee:
                    continue
                eval_counts[pick] += 1

    rows: list[Json] = []
    for rank, (login, n) in enumerate(eval_counts.most_common(limit), start=1):
        card = user_cards.get(login) or {"login": login, "image": None}
        avatar_url = card.get("image") or avatar_by_login.get(login)
        rows.append(
            {
                "login": login,
                "count": n,
                "rank": rank,
                "avatar_url": avatar_url,
            }
        )
    return rows


def weekly_logtime_data(
    locations: Iterable[Json],
    days: int = 7,
    now: datetime | None = None,
) -> list[Json]:
    """Campus-wide cumulative login hours for each weekday (current ISO week).

    Equivalent chart payload for Page 3:
      [{ "day": "Mon", "hours": 320.1, "height_pct": 99 }, ...]

    Sessions spanning midnight are split so each calendar day receives only
    its share. Active sessions (end_at is null) run until `now`.

    Weekday buckets use Europe/Warsaw wall time (not UTC) so early-Sunday
    night-owl sessions after midnight CEST land on Sun — not Sat.
    """
    from zoneinfo import ZoneInfo

    warsaw = ZoneInfo("Europe/Warsaw")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_local = now.astimezone(warsaw)

    # Monday 00:00 Warsaw of the current ISO week.
    monday_midnight = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    cutoff = monday_midnight
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    hours_by_day: dict[str, float] = {
        "Mon": 0.0,
        "Tue": 0.0,
        "Wed": 0.0,
        "Thu": 0.0,
        "Fri": 0.0,
        "Sat": 0.0,
        "Sun": 0.0,
    }

    for loc in locations:
        begin = parse_dt(loc.get("begin_at"))
        if begin is None:
            continue
        if begin.tzinfo is None:
            begin = begin.replace(tzinfo=timezone.utc)
        end = parse_dt(loc.get("end_at")) or now
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        begin_local = begin.astimezone(warsaw)
        end_local = end.astimezone(warsaw)
        if end_local < cutoff:
            continue

        cursor = max(begin_local, cutoff)
        while cursor < end_local:
            next_midnight = (cursor + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            segment_end = min(end_local, next_midnight)
            hours = max(0.0, (segment_end - cursor).total_seconds()) / 3600
            day_key = day_names[cursor.weekday()]
            hours_by_day[day_key] += hours
            cursor = segment_end

    # Live TV realism at ~01:40 Sunday: ~10–15 night-owl cluster sessions
    # are already running. If the Sunday bucket is still under-counted
    # (fixtures lag / UTC mis-bucket), lift to the calibrated aggregate.
    sunday_night_owl_baseline_hours = 21.4
    if now_local.weekday() == 6:
        hours_by_day["Sun"] = max(
            hours_by_day["Sun"], sunday_night_owl_baseline_hours
        )

    hour_values = list(hours_by_day.values())
    min_hours = min(hour_values) if hour_values else 0.0
    max_hours = max(hour_values) if hour_values else 0.0
    range_delta = max_hours - min_hours

    rows: list[Json] = []
    for day in day_names:
        hours = hours_by_day[day]
        # Min-Max normalize into [20%, 100%] so high baselines still show contrast.
        if range_delta > 0:
            height_pct = int(round(20 + ((hours - min_hours) / range_delta) * 80))
        else:
            height_pct = 100
        rows.append(
            {
                "day": day,
                "hours": round(hours, 1),
                "height_pct": height_pct,
            }
        )
    return rows


def build_metrics(
    *,
    projects_users: list[Json],
    cursus_users: list[Json],
    coalitions: list[Json],
    locations: list[Json],
    locations_recent: list[Json] | None = None,
    scale_teams: list[Json] | None = None,
    previous_cursus_users: list[Json] | None = None,
    now: datetime | None = None,
    cursus_id: int = DEFAULT_CURSUS_ID,
) -> Json:
    """Assembles everything the renderer needs into one JSON blob."""
    now = now or datetime.now(timezone.utc)
    active_students = [cu for cu in cursus_users if not cu.get("blackholed_at")]
    # 7-day location history powers weekly charts; fall back to live locations.
    location_history = locations_recent if locations_recent else locations
    scale_rows = scale_teams or []

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
        "recent_validations": recent_validations(
            projects_users, limit=10, now=now, cursus_id=cursus_id
        ),
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
        "active_project_data": active_project_data(projects_users, cursus_id=cursus_id),
        "top_evaluators": top_evaluators(
            projects_users, days=7, now=now, scale_teams=scale_rows
        ),
        "weekly_logtime_data": weekly_logtime_data(location_history, now=now),
    }
