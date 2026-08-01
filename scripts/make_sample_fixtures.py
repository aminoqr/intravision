"""Generates synthetic fixtures shaped like real 42 API responses.

Lets the whole pipeline run - and the TV view be designed - with no credentials and
no quota. Replace with `make fetch-live` output once the app is registered.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"

random.seed(42)

PROJECTS = [
    "libft", "ft_printf", "get_next_line", "Born2beroot", "push_swap", "minitalk",
    "so_long", "pipex", "FdF", "philosophers", "minishell", "NetPractice",
    "cub3d", "miniRT", "CPP Module 00", "ft_irc", "webserv", "Inception",
    "ft_transcendence",
]

LOGINS = [
    "amaciej", "kwrona", "jnowak", "mlewand", "azielin", "pkowals", "twojcik",
    "skaczma", "bmazur", "dkrawcz", "gszymcz", "lwieczo", "nsobier", "owalcza",
    "rjablon", "ustanis", "wkubiak", "zbaranо", "cdabrow", "fmichal",
]

STATUSES = ["finished", "in_progress", "in_progress", "waiting_for_correction"]


def user(login: str) -> dict:
    return {
        "id": abs(hash(login)) % 100000,
        "login": login,
        "displayname": login.capitalize(),
        "image_url": None,
        "image": {"link": None},
    }


def main() -> None:
    now = datetime.now(timezone.utc)

    projects_users = []
    for i in range(180):
        login = random.choice(LOGINS)
        status = random.choice(STATUSES)
        validated = status == "finished" and random.random() < 0.78
        marked = now - timedelta(minutes=random.randint(2, 14 * 24 * 60))
        projects_users.append(
            {
                "id": 10000 + i,
                "occurrence": random.choices([0, 1, 2], weights=[7, 2, 1])[0],
                "final_mark": random.choice([100, 100, 110, 125, 85, 90]) if validated else None,
                "status": status,
                "validated?": validated,
                "marked_at": marked.isoformat() if status == "finished" else None,
                "project": {"id": i, "name": random.choice(PROJECTS)},
                "user": user(login),
            }
        )

    cursus_users = []
    for i, login in enumerate(LOGINS * 12):
        cursus_users.append(
            {
                "id": 20000 + i,
                "level": round(random.triangular(0.5, 14.0, 4.5), 2),
                "grade": random.choice(["Learner", "Member", None]),
                "begin_at": (now - timedelta(days=random.randint(30, 900))).isoformat(),
                "blackholed_at": None if random.random() < 0.9 else now.isoformat(),
                "user": user(f"{login}{i // len(LOGINS) or ''}"),
            }
        )

    # Real Warsaw coalition names/colours (ids/scores are synthetic for offline demo).
    # Never invent campus coalition brands — they show on the Social Space TV.
    coalitions = [
        {
            "id": 459,
            "name": "Lunaria",
            "slug": "lunaria",
            "score": 41200,
            "color": "#52BDFF",
            "image_url": None,
        },
        {
            "id": 458,
            "name": "Orionis",
            "slug": "orionis",
            "score": 38750,
            "color": "#BE2AD1",
            "image_url": None,
        },
        {
            "id": 460,
            "name": "Uniterrax",
            "slug": "uniterrax",
            "score": 35980,
            "color": "#FFCD5A",
            "image_url": None,
        },
    ]

    locations = [
        {
            "id": 30000 + i,
            "begin_at": (now - timedelta(hours=random.randint(1, 8))).isoformat(),
            "end_at": None if i < 37 else now.isoformat(),
            "host": f"w{random.randint(1,4)}r{random.randint(1,6)}p{random.randint(1,20)}",
            "user": user(random.choice(LOGINS)),
        }
        for i in range(60)
    ]

    FIXTURES.mkdir(exist_ok=True)
    for name, rows in [
        ("projects_users", projects_users),
        ("cursus_users", cursus_users),
        ("coalitions", coalitions),
        ("locations", locations),
    ]:
        (FIXTURES / f"{name}.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        print(f"{name}: {len(rows)} rows")

    print(f"\nwritten to {FIXTURES}")


if __name__ == "__main__":
    main()
