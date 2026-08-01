<!-- File: README.md -->

# Intra-Vision — 42 Warsaw Hacks

Passive **Social Space TV** dashboard for celebrating Common Core progress at 42 Warsaw.

**Target display:** 1920×1080 @ 30Hz · no hover-only UI · MagicInfo-compatible web page.

Judging mindset (organizers): working code + honest pitch over polish. This PoC prioritizes a
real API → cache → screen path, with an offline fixture mode if Intra is down during the demo.

## What works

| Piece | Status |
|---|---|
| OAuth2 client-credentials + rate limiter | Yes (`src/ft/client.py`) |
| Live Warsaw fetch (≥2 endpoints) | Yes — projects_users, cursus_users, locations, blocs |
| Processed metrics (not raw dumps) | Yes — validations feed, campus pulse, coalitions, levels |
| SQLite cache; render costs **0** API quota | Yes |
| `POST /refresh` (brief requirement) | Yes — 202, background fetch |
| Offline demo (`make demo`) | Yes — fixtures, 0 external requests |
| Unit tests | `make test` — 24 passing |

## What’s not done / limitations

- **No live deploy required for judging** — demo from `localhost:8000`. Post-win hosting plan
  (Render → MagicInfo) is described in [docs/architecture.md](docs/architecture.md).
- `/v2/campus/:id/stats` returns **403** on our public token — not used.
- Bare `/graph/.../by/day` for projects/cursus returned **422** — lists + local metrics instead.
- Next.js files under `app/` / `components/` are optional stubs, **not** the PoC path.
- Level-up diffs need a prior SQLite snapshot (empty on cold start).

## Stack

- **Python 3** + **FastAPI** + **Jinja2** — one process for fetch orchestration + TV HTML
- **SQLite** — processed metrics cache
- **httpx** — 42 API client
- Secrets stay in `.env.local` (gitignored) — never `NEXT_PUBLIC_*` for UID/SECRET

## Quick start

```bash
make setup
```

Create `.env.local` (never commit it):

```bash
FORTYTWO_APP_UID=...
FORTYTWO_APP_SECRET=...
FT_CAMPUS_ID=67
FT_CURSUS_ID=21
```

Confirm ids anytime: `make resolve-ids` (Warsaw **67**, `42cursus` **21**).

### Offline demo (recommended for pitch backup)

```bash
make demo
# → http://localhost:8000
```

Rebuild metrics from committed fixtures without hitting the API:

```bash
make fetch
make serve
```

### Live data (spend quota carefully — ~11 requests per refresh)

```bash
make fetch-live
make serve
```

### Tests

```bash
make test
```

### Manual refresh while the server is up

```bash
curl -X POST http://localhost:8000/refresh
```

## Screens (TV rotation)

Served from [src/ft/templates/dashboard.html](src/ft/templates/dashboard.html):

1. **Recently validated** — who finished what (celebration feed)
2. **Campus right now** — on campus, validations this week, active students, median level
3. **Coalitions** — Warsaw standings
4. **Level distribution** — anonymous histogram

Header shows an honest **updated … ago** stamp (amber when older than 30 minutes).

Screenshots of working views: [docs/screenshots/](docs/screenshots/).

## Docs (hackathon deliverables)

- [docs/42-api.md](docs/42-api.md) — API research (endpoints, rate limits, quirks, errors)
- [docs/architecture.md](docs/architecture.md) — technical architecture + deployment plan
- [docs/pitch.md](docs/pitch.md) — 5-minute pitch outline
- [plan.md](plan.md) — foundation decisions + official brief notes

## Repo layout (essentials)

```
src/ft/          client, fetch, metrics, store, FastAPI app, TV template
fixtures/        offline / last-known live snapshots
tests/           metrics unit tests (no network)
docs/            deliverables + screenshots
Makefile         setup, demo, fetch, serve, probe, resolve-ids
```
