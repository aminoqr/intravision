"""Resolve campus_id and cursus_id from the 42 API and write them to .env.local.

Uses client credentials from .env.local (FORTYTWO_APP_UID / FORTYTWO_APP_SECRET,
or FT_UID / FT_SECRET). Campus defaults to Warsaw; cursus defaults to the
Common Core slug (42cursus).

Usage:
    PYTHONPATH=src .venv/bin/python scripts/resolve_env_ids.py
    make resolve-ids
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env.local"

# Prefer the Common Core; fall back to legacy "42" if the slug ever changes.
CURSUS_SLUGS = ("42cursus", "42")
DEFAULT_CAMPUS_NAME = "Warsaw"

ENV_KEYS = (
    "NEXT_PUBLIC_CAMPUS_ID",
    "NEXT_PUBLIC_CURSUS_ID",
    "FT_CAMPUS_ID",
    "FT_CURSUS_ID",
)


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def write_env_updates(path: Path, updates: dict[str, str]) -> None:
    """Merge updates into an env file, preserving comments and unrelated keys."""
    if path.exists():
        lines = path.read_text().splitlines()
    else:
        lines = []

    seen: set[str] = set()
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key, _, _ = stripped.partition("=")
        key = key.strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)

    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")

    trailing_newline = "\n" if out else ""
    path.write_text("\n".join(out) + trailing_newline)


def credentials(env: dict[str, str]) -> tuple[str, str]:
    uid = (
        env.get("FORTYTWO_APP_UID")
        or env.get("FT_UID")
        or env.get("FORTYTWO_UID")
        or os.environ.get("FORTYTWO_APP_UID")
        or os.environ.get("FT_UID")
        or ""
    )
    secret = (
        env.get("FORTYTWO_APP_SECRET")
        or env.get("FT_SECRET")
        or env.get("FORTYTWO_SECRET")
        or os.environ.get("FORTYTWO_APP_SECRET")
        or os.environ.get("FT_SECRET")
        or ""
    )
    if not uid or not secret:
        sys.exit(
            "Missing credentials. Set FORTYTWO_APP_UID and FORTYTWO_APP_SECRET "
            f"in {ENV_PATH.name}."
        )
    return uid, secret


def resolve_campus_id(client, campus_name: str) -> int:
    campuses = client.get_json("/v2/campus", {"filter[name]": campus_name})
    if not campuses:
        sys.exit(f"No campus found matching name={campus_name!r}")
    if len(campuses) > 1:
        matches = [(c["id"], c["name"]) for c in campuses]
        print(f"Warning: multiple campuses matched {campus_name!r}: {matches}")
    campus = campuses[0]
    print(f"Campus: id={campus['id']} name={campus['name']!r}")
    return int(campus["id"])


def resolve_cursus_id(client) -> int:
    cursus_list = client.get_json("/v2/cursus", {"page[size]": 100})
    by_slug = {c.get("slug"): c for c in cursus_list if c.get("slug")}

    for slug in CURSUS_SLUGS:
        match = by_slug.get(slug)
        if match:
            print(
                f"Cursus: id={match['id']} slug={match['slug']!r} "
                f"name={match.get('name')!r}"
            )
            return int(match["id"])

    slugs = sorted(by_slug)
    sys.exit(
        f"No cursus with slug in {CURSUS_SLUGS}. Available slugs: {', '.join(slugs)}"
    )


def main() -> None:
    env = load_dotenv(ENV_PATH)
    uid, secret = credentials(env)
    campus_name = (
        env.get("FT_CAMPUS_NAME")
        or os.environ.get("FT_CAMPUS_NAME")
        or DEFAULT_CAMPUS_NAME
    )

    # Import after ROOT is on the path via PYTHONPATH=src (or Makefile).
    from ft.client import FtClient

    with FtClient(uid, secret) as client:
        campus_id = resolve_campus_id(client, campus_name)
        cursus_id = resolve_cursus_id(client)

    updates = {
        "NEXT_PUBLIC_CAMPUS_ID": str(campus_id),
        "NEXT_PUBLIC_CURSUS_ID": str(cursus_id),
        "FT_CAMPUS_ID": str(campus_id),
        "FT_CURSUS_ID": str(cursus_id),
    }
    write_env_updates(ENV_PATH, updates)
    print(f"Wrote {', '.join(ENV_KEYS)} to {ENV_PATH}")


if __name__ == "__main__":
    main()
