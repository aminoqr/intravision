<!-- File: docs/42-api.md -->

# API research — Intra-Vision

Base URL `https://api.intra.42.fr`. OAuth2 client credentials, `public` scope.
Pinned ids: **campus 67** (Warsaw), **cursus 21** (`42cursus`). I resolved them once with
`make resolve-ids` and hard-coded them, since scanning the global campus list on every run is
both wrong and expensive.

## 1. Endpoints used

Auth is `POST /oauth/token` with `grant_type=client_credentials` → `access_token` and
`expires_in` 7200. I re-auth at about 90% of that. `GET /oauth/token/info` I only used to debug
why a call was failing.

One live refresh (`src/ft/fetch.py`) makes these calls:

| Endpoint | Params | Fields I actually read |
|---|---|---|
| `/v2/projects_users` | `filter[campus]=67`, `range[marked_at]=<14d>,today`, `sort=-marked_at` | `user{login,displayname,image.link}`, `project{id,name,slug}`, `final_mark`, `validated?`, `status`, `marked_at`, `occurrence`, `cursus_ids` |
| `/v2/projects_users` | `filter[campus]=67`, `filter[status]=in_progress` | same shape; feeds the active-projects panel |
| `/v2/cursus/21/cursus_users` | `filter[campus_id]=67` | `level`, `grade`, `begin_at`, `blackholed_at`, `user` |
| `/v2/campus/67/locations` | `filter[active]=true` | `host` (e.g. `c2r9s2`), `begin_at`, `end_at` (null = still seated), `user` |
| `/v2/campus/67/locations` | `range[begin_at]=<7d>,now`, `sort=-begin_at` | same shape; feeds weekly logtime + cluster residents |
| `/v2/blocs` | `filter[campus_id]=67` | embedded `coalitions[]`: `id`, `name`, `slug`, `score`, `color`. Falls back to `/v2/coalitions` if blocs comes back empty |
| `/v2/scale_teams` | `filter[campus_id]=67`, `filter[filled]=true`, `range[filled_at]=<45d>,today` | `corrector.login`, `filled_at` |

One thing that cost me time: `projects_users` wants `filter[campus]`, not `filter[campus_id]` like
the others. Same API, different key.

Rows from the last live pull, saved in `fixtures/`: projects_users 1119, cursus_users 575,
active locations 7, 7-day locations 889, scale_teams 1000, coalitions 3.

Pagination is `page[size]=100`, stop when `X-Total` says I have everything, hard cap at
`FT_MAX_PAGES=20` per collection so a bad filter can't run away with the budget.

Probed and then dropped (`make probe`, report is gitignored):

- `/v2/campus/67/stats` → **403**, and `X-Application-Roles` comes back `None`. So no campus-stats panel.
- `/v2/projects_users/graph/on/marked_at/by/day` → **422**.
- `/v2/cursus_users/graph/on/level/by/day` → **422**. I compute the level aggregates myself instead.
- `/v2/locations/graph/on/begin_at/by/day` → 200, but the plain list already gives me what I show.

The `/graph` endpoints were the thing I most wanted to work, because server-side aggregation is
exactly how you beat a request budget. The bare paths don't take, and I didn't find the right
parameters in time.

## 2. Refresh strategy + rate limits

2 req/s and 1200 req/hour on my app tier (roles: `None`). That number decided the whole architecture.

- `src/ft/client.py` is one serialized queue with one worker and a 500 ms floor between dispatches,
  plus a rolling hourly window capped at 90% of 1200. Nothing in the codebase can fan out in
  parallel, because nothing else gets to hold the HTTP client.
- On **429** I read `Retry-After` (both the seconds form and the HTTP-date form) and pause the whole
  queue for that long plus 100 ms, then retry the same call. Pausing only the failed call is the
  easy mistake — the next one in line just 429s again. On 401 I drop the token and re-auth;
  5xx gets exponential backoff.
- The browser never talks to Intra. The page polls my own `GET /api/metrics` every 30 s and that
  reads SQLite. Rendering the dashboard costs zero quota.
- `POST /refresh` returns **202** straight away and runs the fetch in a FastAPI background task,
  behind a lock so leaning on the button doesn't stack refreshes.

Measured, not estimated: the last live refresh used **40 requests** (`requests_used`, stored
alongside the metrics).

Where this is currently wrong: `scripts/serve_with_refresh.sh` re-fetches every 120 s. At 40
requests a go that's ~1200/hour — exactly the ceiling, with nothing left over for a probe or a
manual refresh. It's a demo setting so the screen visibly moves while someone is watching it.
A real deploy needs the 10-minute interval (`FT_REFRESH_SECONDS=600`, ≈240/hour).

## 3. Data quirks

### Anonymised and half-empty users

Every user object goes through one builder, `_user_card` in `src/ft/metrics.py`, so the fallbacks
live in one place: no `login` renders as `"unknown"`, no `displayname` falls back to `login`, and
no avatar becomes a generated SVG with the student's initial rather than a broken `<img>`.

Rails-shaped things I hit for real: `phone` is the literal string `"hidden"` on all 1119 rows I
pulled, `staff?` and `validated?` keep the `?` in the JSON key, avatars show up as either
`image.link` or `image_url` depending on where you got the user from, and timestamps come back
both as `…Z` and `…+01:00` (normalised in `parse_dt`).

To be straight about it: I never saw a genuinely anonymised account (past `anonymize_date`) in the
Warsaw data. The `"unknown"` path is what would catch one, and it's unit-tested, but I haven't
watched it happen against the live API.

### Projects with retries and multiple versions

137 of those 1119 rows had `occurrence > 0`, so retries are common enough to matter. I key
everything on `project.name` / `slug` and never on a session id, which means one project stays one
bar even across attempts and cursus variants. `recent_validations` does compute
`attempt = occurrence + 1`, but the current TV layout doesn't print it — the field is in the
metrics payload and nothing reads it yet.

I also drop Piscine and Exam Rank rows by name and slug. Without that they dominate the Warsaw
feed even when no piscine is running, and the panel stops being about Common Core.

## 4. Errors and the API being down

- One endpoint fails mid-refresh: `_safe()` logs it and returns `[]` for that collection only, so
  one panel goes quiet and the rest still update.
- The whole refresh throws: `ft.app._run_refresh` catches it, the previous SQLite snapshot stays on
  screen, and the sync stamp keeps ageing and turns amber past 30 minutes. I'd rather the TV admit
  it's stale than quietly show yesterday as if it were now.
- Intra down during the demo: `make demo` rebuilds every number from the committed `fixtures/` with
  zero external calls.
- The 403 and the two 422s above are permanent at my token tier. I deleted those panels instead of
  filling them with something I can't actually source.
- Browser loses my server: the poll fails silently and the last painted frame stays up.

## 5. Auth

Client credentials only — there's no user to log in on an unattended TV, so the authorization-code
flow would be dead weight. The secret sits in `.env.local` (gitignored), gets read server-side in
`ft.config`, and never reaches the browser or a `NEXT_PUBLIC_` variable.

See [architecture.md](architecture.md) for how the pieces fit together.
