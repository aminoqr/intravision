<!-- File: docs/architecture.md -->

# Technical architecture — Intra-Vision

## 1. What it is

A read-only campus dashboard for the 42 Warsaw Social Space TV. One 1920×1080 page that rotates
through three views: who's in the cluster right now, what the campus just validated, and what
everyone is working on. Nobody interacts with it — no login, no mouse, no clicking. A background
job pulls Warsaw data from Intra into SQLite, and the page only ever reads SQLite.

## 2. Deployment target

**What I'm actually running:** local only. `make serve` starts uvicorn on `http://localhost:8000`
plus a background loop that re-fetches live Intra data every 120 s, and I show it in a browser
window at 1920×1080. Nothing is deployed anywhere.

**What I'd do to put it on the TV.** The organizers said only the winning team gets paid to deploy,
so this is a plan and I'm not pretending otherwise:

1. Render Python web service from this repo, start command
   `PYTHONPATH=src uvicorn ft.app:app --host 0.0.0.0 --port $PORT`.
2. Env vars `FORTYTWO_APP_UID`, `FORTYTWO_APP_SECRET`, `FT_CAMPUS_ID=67`, `FT_CURSUS_ID=21`.
3. Persistent disk for `data/dashboard.db`, so a restart still has the last snapshot to render.
4. Cron every 10 minutes running `python -m ft.fetch` — not the 120 s demo loop, for the rate-limit
   reason in [42-api.md](42-api.md).
5. MagicInfo web-content slot pointed at the Render HTTPS URL, scheduled on the Social Space display.

Nothing exotic is needed here: MagicInfo loads a URL, so what it wants is a normal webpage, which
is what this is. The TV facts I designed against (1920×1080 @ 30 Hz, has internet) come from the
opening presentation — I haven't tested on the actual screen.

## 3. Stack

- **FastAPI + Jinja2** — one process serves the HTML and owns the fetch job. No build step and no
  bundler, so fixing something at 3am is one file and a reload.
- **SQLite (WAL)** — the fetcher writes while the renderer reads. It's also the outage story: if
  Intra dies, the last snapshot is still sitting on disk, and `fixtures/` rebuild the same database
  with no network at all.
- **httpx** — one client, one request queue, easy place to put the rate limiter in front of everything.
- **Vanilla JS** polling `/api/metrics` every 30 s — it patches the DOM in place instead of
  reloading, which matters on a 30 Hz panel that runs all day.

I started this on Next.js and Tailwind, then pivoted on day one. The hard part here was never the
frontend — it's OAuth, pagination, rate limiting and caching, which is all server work. Staying on
Next.js meant either running a JS server next to a Python fetch layer, or writing the entire 42
client in TypeScript. Python got the pipeline working in hours, and the offline fixture mode came
out of it for free.

## 4. Services + data flow

```
                        (every 120s demo / 10min planned)
  api.intra.42.fr  <────────────  ft.fetch  ──> ft.metrics (pure) ──┐
        ▲                            │                             │
        │                            │ ft.client                   ▼
        └── OAuth + campus-scoped ───┘ queue: 2 req/s, 1200/hr,  SQLite
            GETs, ~40 per refresh      429 Retry-After pause     data/dashboard.db
                                                                    │
                                                            read only│
                                                                    ▼
   TV / browser ──── GET /  ──────────────────────────────────>  ft.app (FastAPI)
                └── GET /api/metrics every 30s ────────────────>  Jinja + dashboard.html
   Operator ──── POST /refresh ──> 202 + background task ───────>  ft.fetch
```

There is no arrow from the browser to `api.intra.42.fr`, and that's the whole design. Everything
else follows from the request budget.

| Piece | Path | Role |
|---|---|---|
| API client | `src/ft/client.py` | token, throttled queue, pagination, retries |
| Fetch job | `src/ft/fetch.py` | pull collections → metrics → store, every call wrapped in `_safe()` |
| Metrics | `src/ft/metrics.py` | pure JSON → display numbers, no I/O, so it's all unit-tested |
| Store | `src/ft/store.py` | SQLite WAL, snapshots + history + meta |
| HTTP | `src/ft/app.py` | `/`, `/api/metrics`, `/api/weather`, `/refresh`, `/healthz` |
| TV UI | `src/ft/templates/dashboard.html` | three rotating views, 25–30 s each |

## 5. Failure behaviour

| Situation | What happens |
|---|---|
| One endpoint errors | `_safe()` returns `[]`, that panel empties, the rest update |
| Refresh throws entirely | Old snapshot kept, sync stamp ages and goes amber after 30 min |
| 429 | Queue pauses for `Retry-After` + 100 ms, then retries |
| Process restart | DB on disk still has the last metrics, so the page renders immediately |
| Two refreshes at once | Lock in `ft.app` skips the second |

## 6. Known limitations

- Data is up to one refresh interval stale, by design.
- `/v2/campus/:id/stats` → 403 and two `/graph` paths → 422 at my token tier, so those panels
  don't exist rather than being faked.
- The 120 s demo refresh loop sits right at the hourly ceiling. Fine while someone is watching it,
  wrong for production.
- Single SQLite file, single process. Right for one TV, not for many campuses.
- Coalition scores get fetched and stored, but no current view shows them.
- Never run on the real Social Space TV or through MagicInfo.
