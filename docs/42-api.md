<!-- File: docs/42-api.md -->

# Using the 42 API

Practical guide for this project. Official reference: https://api.intra.42.fr/apidoc

Everything below marked **[verified]** comes from the official guides or from our live probe on
**1 Aug 2026**. Earlier draft assumptions that we measured are updated in place.

Base URL: `https://api.intra.42.fr` · RESTful, JSON over HTTPS, OAuth 2.0.

---

## 1. Create an application

At https://profile.intra.42.fr/oauth/applications:

- **Name** — must be explicit. `test` and `app` get rejected.
- **Redirect URI** — only used by the web application flow. For a server-side dashboard, any valid
  address works; you won't use it.
- **Scopes** — defaults to `public`. Add only what you need; changeable later.
- **Public** — whether other users can see the app.

You get a **client uid** (public identifier) and a **client secret** (server-side only, never
shipped to a browser).

---

## 2. Authentication

Two flows. Pick based on whether a human is involved.

### Client credentials — use this one

No user, server-side, `public` scope. Correct for a TV dashboard, which has nobody logged in.

```bash
curl -X POST \
  --data "grant_type=client_credentials&client_id=$UID&client_secret=$SECRET" \
  https://api.intra.42.fr/oauth/token
```

```json
{
  "access_token": "42804d1f...",
  "token_type": "bearer",
  "expires_in": 7200,
  "scope": "public",
  "created_at": 1443451918
}
```

**Tokens expire after 7200 seconds (2 hours).** [verified] A long-running dashboard must refresh.
Refresh proactively at ~90% of lifetime rather than reacting to a 401 — a failed refresh mid-render
is a blank TV in a public space.

### Authorization code (web application flow) — only if a user logs in

Not needed for the TV display. Documented here in case a companion "my progress" view is built.

**Step 1** — redirect the user to:

```
https://api.intra.42.fr/oauth/authorize
  ?client_id=YOUR_UID
  &redirect_uri=https%3A%2F%2Fexample.com%2Fcallback
  &response_type=code
  &scope=public
  &state=UNGUESSABLE_RANDOM_STRING
```

**Step 2** — 42 redirects back to `redirect_uri?code=…&state=…`.

**Verify `state` matches what you sent.** [verified] If it doesn't, the request came from a third
party — abort. This is the CSRF defense; skipping the comparison silently removes it.

**Step 3** — exchange server-side, over TLS:

```bash
curl -X POST \
  -F grant_type=authorization_code \
  -F client_id=$UID \
  -F client_secret=$SECRET \
  -F code=$CODE \
  -F redirect_uri=https://example.com/callback \
  https://api.intra.42.fr/oauth/token
```

**Scope gotcha** [verified]: application-level and token-level scopes are distinct. If the user
already holds a valid token for your app, the authorization screen is skipped and the flow completes
with the *previous* scopes — a newly requested scope will appear to be silently ignored. The docs
call this "a point of friction for some developers."

### Making authenticated requests

```bash
curl -H "Authorization: Bearer $TOKEN" https://api.intra.42.fr/v2/me
```

An `access_token` query parameter also works where headers can't be set, but it leaks into logs and
referrer headers. Use the header.

### Inspecting a token

```bash
curl -H "Authorization: Bearer $TOKEN" https://api.intra.42.fr/oauth/token/info
```

```json
{"resource_owner_id":74,"scopes":["public"],"expires_in_seconds":7174,
 "application":{"uid":"3089cd94..."},"created_at":1439460680}
```

First thing to check when a request 401s or returns less data than expected — confirm the token's
actual scopes before blaming the endpoint.

---

## 3. Rate limits — the constraint that shapes everything

**2 requests/second and 1200 requests/hour.** [verified]

For a campus dashboard this is the dominant design fact, not a footnote:

| Approach | Requests | Verdict |
|---|---|---|
| `cursus_users` for ~1000 students at `page[size]=100` | ~10 | fine |
| Per-user detail loop over 1000 students | ~1000 | burns 83% of the hourly budget |
| Per-render fetching, 30s refresh | 120/hr minimum, ×N panels | fails within the hour |

Rules that follow:

1. **Never fetch during render.** A background job populates a store; the display reads only the
   store. Page loads must cost zero API requests.
2. **Cache aggressively, with a visible timestamp.** Stale data honestly labeled is fine on a TV.
3. **Develop against fixtures.** Iterating against the live API will exhaust the budget and block
   work for the rest of the hour — during a 24h hackathon that is unrecoverable.
4. **Count your requests.** Log a running total against 1200/hr from the start.
5. **Prefer bulk and `/graph` endpoints** over per-entity loops.

Elevated limits come with the `Official App` or `Certified App` roles, granted manually by 42 staff
on request by mail. Not available on a hackathon timeline — design for the default.

### Roles [verified]

Your app's roles appear in the `X-Application-Roles` response header. Common ones: `Alpha`, `Beta`,
`Official App`, `Certified App`, `Moderator`, `Basic Tutor`, `Basic Staff`. Assume `None`.

---

## 4. Pagination

Default **30 items per page**, max **100**. [verified] Every list endpoint is paginated — code that
reads the first element without handling pagination breaks as soon as the campus exceeds 30 students.

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://api.intra.42.fr/v2/cursus/42/users?page[number]=2&page[size]=100"
```

Aliases: `page` for `page[number]`, `per_page` for `page[size]`.

### Read the headers, don't guess

```
Link: <...?page=1>; rel="first", <...?page=1>; rel="prev",
      <...?page=586>; rel="last", <...?page=3>; rel="next"
X-Page: 2
X-Per-Page: 30
X-Total: 17570
```

`X-Total` gives the exact count up front — one request tells you how many pages to expect. Looping
until an empty array comes back works but wastes a request every time, and at 1200/hr that adds up.

Also present: `X-Application-Id`, `X-Application-Name`, `X-Application-Roles`, `X-Request-Id`
(quote this if you need to ask 42 staff about a specific failure).

### Filtering and sorting [verified 1 Aug 2026]

On our public client-credentials token these work as used by `ft.fetch`:

| Parameter | Endpoint | Result |
|---|---|---|
| `filter[name]=Warsaw` | `/v2/campus` | OK → campus **id 67** |
| `filter[campus_id]=67` | `/v2/cursus/21/cursus_users` | OK · `X-Total=575` · `page[size]=100` honoured |
| `filter[campus]=67` | `/v2/projects_users` | OK |
| `sort=-marked_at` | `/v2/projects_users` | OK |
| `range[marked_at]=a,b` | `/v2/projects_users` | OK (keeps the 14-day window cheap) |
| `filter[active]=true` | `/v2/campus/67/locations` | OK · 27 open sessions at probe time |
| `filter[campus_id]=67` | `/v2/blocs` | OK · Warsaw coalitions embedded |

**Caveat:** unscoped `GET /v2/projects_users?sort=-marked_at` reports millions in `X-Total` — always pair sort/range with campus filter.

---

## 5. Endpoints for this project

739 endpoints exist. The dashboard needs roughly a dozen.

### Always scope to campus

Most collections have a `/v2/campus/:campus_id/...` variant. **Use it.** Unscoped queries return
every 42 campus worldwide — wrong data and a rate-limit disaster simultaneously.

Resolve once, then hard-code (confirmed **1 Aug 2026**):

```bash
# Warsaw → id 67
curl -H "Authorization: Bearer $TOKEN" "https://api.intra.42.fr/v2/campus?filter[name]=Warsaw"
# Common Core slug 42cursus → id 21 (legacy slug "42" is id 1)
curl -H "Authorization: Bearer $TOKEN" "https://api.intra.42.fr/v2/cursus?page[size]=100"
```

Or: `make resolve-ids`

### By dashboard goal

**Peer progress**
- `GET /v2/cursus/:cursus_id/cursus_users` — the main leaderboard source. Carries `level`, `grade`,
  `begin_at`, `blackholed_at`. Level is precomputed; don't recalculate it.
- `GET /v2/campus/:campus_id/users`

**Recently finished projects**
- `GET /v2/projects_users` — validation status and marks. Variants:
  `/v2/projects/:project_id/projects_users`, `/v2/users/:user_id/projects_users`
- `GET /v2/teams`, `GET /v2/teams_uploads` — per-team marks from bot correction (Moulinette)

**Community overview**
- `GET /v2/campus/:campus_id/stats`
- `GET /v2/coalitions`, `GET /v2/coalitions_users` — coalition standings
- `GET /v2/campus/:campus_id/locations` — who is physically on campus now
- `GET /v2/achievements`, `GET /v2/titles` — celebratory content

**Lookups (fetch once, cache, never per-render)**
- `GET /v2/cursus`, `GET /v2/campus`, `GET /v2/projects`, `GET /v2/cursus/:cursus_id/projects`

### `/graph` endpoints [verified 1 Aug 2026]

Many resources expose `/graph(/on/:field(/by/:interval))`. We probed four:

| Path | Result |
|---|---|
| `/v2/projects_users/graph/on/marked_at/by/day` | **422** Unprocessable — needs more params than the bare path |
| `/v2/cursus_users/graph/on/level/by/day` | **422** same |
| `/v2/locations/graph/on/begin_at/by/day` | **200** OK |
| `/v2/scale_teams/graph/on/created_at/by/day` | **200** OK (huge `X-Total`; not used in PoC) |

So `/graph` is **not** a free win for our two main panels without further parameter discovery.
The PoC stays on paginated list endpoints + local metrics, which already fit the hourly budget
(~11 requests per live refresh).

Raw probe table: regenerate anytime with `make probe` → `docs/api-probe-results.md` (gitignored,
regenerable).

### Elevated / blocked endpoints [verified]

| Endpoint | Result |
|---|---|
| `GET /v2/campus/67/stats` | **403** Forbidden on public client-credentials |
| `GET /v2/achievements` | **200** OK (usable if we want a panel later) |

App roles header observed: `X-Application-Roles: None`.

---

## 6. Response shapes

`GET /v2/me` is the fastest auth sanity check and the reference for user object structure:

```json
{
  "id": 56911, "login": "30_1", "displayname": "Philibert Edago",
  "image_url": "https://cdn.intra.42.fr/userprofil/pedago.jpg",
  "correction_point": 3, "wallet": 5, "location": null, "staff?": false,
  "cursus": [
    { "cursus": { "id": 1, "name": "42", "slug": "42", "kind": "normal" },
      "level": 0.0, "grade": "cadet", "end_at": null,
      "projects": [], "skills": [] }
  ],
  "campus": [
    { "id": 1, "name": "Paris", "time_zone": "Paris", "users_count": 5929 }
  ],
  "achievements": [], "titles": [], "partnerships": [], "patroned": []
}
```

Things that bite:

- **`level` and `grade` are per-cursus, not per-user.** Always index into the right `cursus[]` entry
  (match on `slug == "42"`). A student in both `42` and `piscine-c` has two levels.
- **`staff?` keeps its Ruby question mark** as a literal JSON key. Won't work as a plain identifier
  in most languages.
- **`phone` may be the string `"hidden"`**, not `null`. Expect sentinel strings where you'd expect
  nulls; this is Rails-shaped JSON throughout.
- **`image_url`** is CDN-hosted and safe to hotlink on the display.
- `users_count` on a campus object is a free community-size metric.

Relation endpoints (`/v2/cursus/:id/users`) return thin objects — `id`, `login`, `url`, `end_at`.
Full detail requires following `url`, which is a per-user request. Prefer `cursus_users` over
`cursus/:id/users` when you need level data, since it carries the fields inline.

---

## 7. Working checklist

```
[x] App created; uid + secret in env vars, secret never client-side
[x] Client-credentials token obtained
[x] Token refresh before 7200s expiry (ft.client @ 90% lifetime)
[x] Rate limiter: 2 req/s, running counter against 1200/hr
[x] Pagination via X-Total / Link, page[size]=100
[x] Warsaw campus id (67) and 42cursus id (21) resolved and pinned
[x] /graph response shapes probed — 2×422, 2×200; lists used instead for main panels
[x] Elevated-access endpoints identified (campus stats → 403)
[x] Fixtures dumped for offline development (live Warsaw snapshot committed)
[x] Cache layer between fetch and render; render makes zero API calls
[x] Stale-cache fallback with visible timestamp for API outages
[x] POST /refresh returns 202 and updates last_refresh (verified 1 Aug 2026)
```

---

## Reference

- API docs — https://api.intra.42.fr/apidoc
- Register an app — https://profile.intra.42.fr/oauth/applications
- OAuth2 RFC, token endpoint — RFC 6749 §3.2
- Architecture — [architecture.md](architecture.md)
- Regenerable probe dump — `make probe` → `docs/api-probe-results.md`
