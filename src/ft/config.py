"""Configuration from environment. Secrets never live in the repo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = ROOT / "fixtures"
DEFAULT_DB = ROOT / "data" / "dashboard.db"


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader so there's no dependency for one feature."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv(ROOT / ".env")
_load_dotenv(ROOT / ".env.local")


@dataclass(frozen=True)
class Config:
    uid: str
    secret: str
    base_url: str
    campus_id: int
    cursus_id: int
    db_path: Path
    refresh_seconds: int
    # Bounds the cost of every campus-wide fetch. Raise deliberately, not by accident.
    max_pages: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            uid=os.environ.get("FT_UID")
            or os.environ.get("FORTYTWO_APP_UID")
            or os.environ.get("FORTYTWO_UID")
            or "",
            secret=os.environ.get("FT_SECRET")
            or os.environ.get("FORTYTWO_APP_SECRET")
            or os.environ.get("FORTYTWO_SECRET")
            or "",
            base_url=os.environ.get("FT_BASE_URL", "https://api.intra.42.fr"),
            # 42 Warsaw. Verify with `make campus` before trusting it.
            campus_id=int(os.environ.get("FT_CAMPUS_ID", "53")),
            # Cursus 21 is the current Common Core ("42cursus"); cursus 1 is the legacy "42".
            cursus_id=int(os.environ.get("FT_CURSUS_ID", "21")),
            db_path=Path(os.environ.get("FT_DB_PATH", str(DEFAULT_DB))),
            refresh_seconds=int(os.environ.get("FT_REFRESH_SECONDS", "600")),
            max_pages=int(os.environ.get("FT_MAX_PAGES", "20")),
        )
