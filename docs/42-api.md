<!-- File: docs/42-api.md -->

# API research — Intra-Vision (42 Warsaw Hacks)

**Deliverable 1.** Shows we understand the 42 Intra API: which endpoints we call, what they
return, how we stay inside rate limits, how we handle quirky / anonymised data, and what happens
when Intra is down.

Base URL: `https://api.intra.42.fr` · OAuth2 client credentials · scope `public`  
Pinned ids (resolved live **1 Aug 2026**): **campus 67 (Warsaw)**, **cursus 21 (`42cursus`)**.  
Official reference: https://api.intra.42.fr/apidoc

---

## 1. Endpoints we use + what each returns

### Auth (every live session)

| Endpoint | Method | Returns (fields we use) |
|---|---|---|
| `/oauth/token` | `POST` `grant_type=client_credentials` | `access_token`, `expires_in` (~7200), `scope` |
| `/oauth/token/info` | `GET` (debug) | `scopes`, `expires_in_seconds`, `application.uid` |

Bearer header on all `/v2/*` calls: `Authorization: Bearer <token>`.

### One-time id resolution (`make resolve-ids`)

| Endpoint | Params | Returns |
|---|---|---|
| `/v2/campus` | `filter[name]=Warsaw` | Campus objects → we take `id` (**67**), `name`, `users_count` |
| `/v2/cursus` | `page[size]=100` | Cursus list → match `slug == "42cursus"` → **id 21** |

### Every live refresh (`make fetch-live` / `POST /refresh` / cron)

Measured cost: **~11 requests** per full Warsaw refresh (1 Aug 2026).

| Endpoint | Params | What data each returns (fields we consume) |
|---|---|---|
| `/v2/projects_users` | `filter[campus]=67`, `range[marked_at]=<14d>,today`, `sort=-marked_at`, `page[size]=100` | Relation rows: nested `user` (login, displayname, image.link), nested `project` (id, name, slug), `final_mark`, `validated?`, `status`, `marked_at`, `occurrence` (0-indexed retry count), `cursus_ids` |
| `/v2/cursus/21/cursus_users` | `filter[campus_id]=67`, `page[size]=100` | Progress rows: `level`, `grade`, `begin_at`, `blackholed_at`, nested `user`, `cursus_id` |
| `/v2/campus/67/locations` | `filter[active]=true`, `page[size]=100` | Open cluster sessions: `host`, `begin_at`, `end_at` (null while active), `campus_id`, nested `user` |
| `/v2/blocs` | `filter[campus_id]=67` | Bloc for Warsaw with embedded `coalitions[]`: `id`, `name`, `slug`, `score`, `color`, `image_url` (Lunaria / Orionis / Uniterrax) |

Pagination: we honour `X-Total` / `Link` and page until done (capped by `FT_MAX_PAGES`). Default page size is 30; we request 100 where supported — verified for `cursus_users` (`X-Total≈575` → 6 pages).

### Example shapes (from live Warsaw fixtures)

**`projects_users` (trimmed):**

```json
{
  "occurrence": 0,
  "final_mark": 125,
  "status": "finished",
  "validated?": true,
  "marked_at": "2026-07-25T19:43:41.054Z",
  "project": { "id": 1983, "name": "Inception", "slug": "inception" },
  "user": {
    "login": "akacprzy",
    "displayname": "Artur Kacprzycki",
    "phone": "hidden",
    "image": { "link": "https://cdn.intra.42.fr/users/.../akacprzy.jpg" }
  }
}
```

**`cursus_users` (trimmed):** `level`, `grade`, `blackholed_at`, nested `user`, `skills[]`.

**`locations` (trimmed):** `host` (e.g. `c2r9s2`), `end_at: null` while seated, nested `user` (includes `anonymize_date` for future erasure — see quirks).

**`blocs` → coalitions:** `Lunaria` / `Orionis` / `Uniterrax` with `score` and brand `color`.

### Probed but **not** used (honest limitations)

| Endpoint | Result (1 Aug 2026) | Implication |
|---|---|---|
| `/v2/campus/67/stats` | **403** Forbidden (`X-Application-Roles: None`) | No campus-stats panel |
| `/v2/projects_users/graph/on/marked_at/by/day` | **422** | Bare `/graph` path unusable without extra params |
| `/v2/cursus_users/graph/on/level/by/day` | **422** | Same — we aggregate levels client-side from `cursus_users` |
| `/v2/locations/graph/on/begin_at/by/day` | **200** | Works, but list+filter already covers “who’s here” |
| `/v2/achievements` | **200** | Available later; not in PoC panels |

Regenerate the raw probe table anytime: `make probe` → `docs/api-probe-results.md` (gitignored).

---

## 2. Rate limits + refresh strategy (explicit)

### Limits [verified]

| Limit | Value |
|---|---|
| Per second | **2 requests/s** |
| Per hour | **1200 requests/hour** |
| Our app roles | `None` (default tier) |

### Strategy we chose: **cached near-real-time** (not real-time, not daily)

| Layer | Cadence | Hits 42 API? |
|---|---|---|
| **Fetcher** (`ft.fetch`) | ~**every 10 minutes** via cron/CLI, or on demand via `POST /refresh` | **Yes** — ~11 req / cycle |
| **TV / browser** | Polls `GET /api/metrics` every **30 seconds** | **No** — reads SQLite only |
| **OAuth token** | Re-acquired at ~90% of 7200s lifetime | Occasional `POST /oauth/token` |

Budget math: 11 req × 6 cycles/hour ≈ **66 req/hour ≈ 5.5% of 1200**. Leaves headroom for demos and probes.

**Rejected alternatives:**

- **Real-time (fetch Intra on every browser poll)** — burns quota; one stuck TV tab can lock the app for an hour.
- **Daily snapshot only** — too stale for a “celebration” board in the Social Space.
- **Per-student fan-out** (`GET /users/:id` × hundreds) — would exhaust the hourly budget in one refresh.

Client enforces the 2/s cap and backs off on **429** (`Retry-After`) and 5xx.

---

## 3. Data quirks

### Anonymised / incomplete / closed students

User objects can be thin or awkward:

- `phone` is often the string **`"hidden"`**, not `null`.
- `staff?` keeps its **Ruby `?`** as a JSON key.
- Avatars may be missing; some accounts will eventually hit `anonymize_date` / `data_erasure_date`.
- Display name can be absent while `login` remains.

**How we handle it** (`src/ft/metrics.py` → `_user_card`):

| Condition | Behaviour |
|---|---|
| Missing `login` | Show `"unknown"` — never crash the panel |
| Missing `displayname` | Fall back to `login` |
| Missing `image` / `image.link` | Empty avatar circle (no broken `<img>`) |
| Not validated / no `marked_at` | Skip row in the celebration feed |
| Aggregate panels (levels, coalitions) | No per-student PII required |

We **do not invent** names or marks. If Intra later returns fully anonymised shells, they degrade to `"unknown"` rather than blanking the TV.

### Projects with multiple versions / sessions / attempts

Intra models this in several ways:

1. **`project_session`** — same project can have different rules per cursus/campus.  
2. **`occurrence`** — how many times the student has attempted the project (0-indexed).  
3. Multiple `projects_users` rows over time for the same `project.id`.

**How we handle it:**

| Concern | Approach |
|---|---|
| Don’t double-count the same Common Core project as different products | Key display/popularity by **`project.name` / `slug` / `project.id`**, not by session id |
| Show persistence, not noise | Map `occurrence` → **attempt N** on the TV when N > 1 |
| Celebration feed | Only rows with `validated? == true` (also accept `validated`) and a parseable `marked_at` |
| Time window | Live fetch bounds with `range[marked_at]` over the last **14 days** so the collection stays finite |

### Other Rails-shaped quirks we coded for

- Timestamps arrive as `…Z` or `…+01:00` — normalised in `parse_dt`.
- `validated?` vs `validated` — both accepted.
- Image may be `image_url` **or** nested `image.link` — both accepted.
- Always **campus-scope** filters: unscoped `projects_users` reports millions in `X-Total`.

---

## 4. Error handling — if the API is down

Designed so a Social Space TV **never goes blank** because Intra hiccuped.

| Failure | What happens |
|---|---|
| One endpoint fails mid-refresh | `_safe()` in `ft.fetch` logs it, returns `[]` for that collection; **other panels still update** |
| Entire live refresh throws (auth down, network down) | `ft.app._run_refresh` catches it; **previous SQLite metrics are kept**; stamp ages |
| **429** rate limit | Client waits `Retry-After` / exponential backoff |
| **401** expired token | Client re-acquires token and retries |
| **403** (e.g. campus stats) | Endpoint not used; documented limitation |
| Browser can’t reach our server briefly | JS poll fails quietly; **last painted frame stays**; stamp keeps aging |
| Intra down during the **pitch** | `make demo` / committed `fixtures/` rebuild the store with **0** external calls; screenshots in `docs/screenshots/` as further backup |

Honesty on screen: header shows **`updated … ago`**, turns **amber** after 30 minutes so viewers know the data is stale rather than silently frozen forever.

---

## 5. Auth summary (how we talk to Intra)

- **Flow:** client credentials only (no user login — correct for a passive TV).
- **Secret:** server-side env (`.env.local`) — never `NEXT_PUBLIC_*`, never sent to the browser.
- **Token lifetime:** ~7200s; refresh at ~90% lifetime in `ft.client`.
- Web application / authorization-code flow: **not used**.

---

## 6. Working checklist

```
[x] Endpoints listed with returned fields (§1)
[x] Refresh strategy stated explicitly: 10-min server fetch + 30s local poll (§2)
[x] Anonymised / incomplete students handled (§3)
[x] Multi-version / multi-attempt projects handled (§3)
[x] API-down behaviour: cache + fixtures + amber stamp (§4)
[x] Live probe: stats 403, some /graph 422, Warsaw coalitions via blocs
[x] ≥2 live endpoints in the PoC with error handling
```

---

## Reference

- API docs — https://api.intra.42.fr/apidoc  
- Register an app — https://profile.intra.42.fr/oauth/applications  
- Architecture — [architecture.md](architecture.md)  
- Pitch notes — [pitch.md](pitch.md)  
- Implementation — `src/ft/client.py`, `src/ft/fetch.py`, `src/ft/metrics.py`, `src/ft/app.py`  
