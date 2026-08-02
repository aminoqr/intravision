<!-- File: docs/pitch.md -->

# Pitch script — Intra-Vision (5:00)

Demo from `http://localhost:8000` at 1920×1080. Have `make demo` ready as the offline fallback.
Backup: `docs/screenshots/`. Let the carousel rotate on its own — do not touch the keyboard.

Timings are speaking marks. Total spoken ≈ 800 words.

---

## 0:00 – 0:25 — Cold open

> Everyone in this room has an Intra account. And every one of us uses it to look at exactly one
> thing: ourselves. Our level, our projects, our blackhole.
>
> Intra is a personal dashboard. Nobody at 42 has a *campus* dashboard.
>
> So we built one, for the TV in the Social Space. It's called **Intra-Vision**. Everything you're
> about to see is live from the 42 API, and none of it is visible on Intra today.

*(Have the dashboard already open on Page 1.)*

---

## 0:25 – 1:05 — Page 1: who's here right now

> **"Who's cooking"** — these are real faces, of real students, sitting in the cluster right now.
> Not a count. Faces. Because when you walk into the Social Space at 2am and see four people on
> that screen, you know you're not alone in the building.
>
> On the right, **cluster occupancy** — four out of a hundred fifty-two seats. Broken down by
> cluster: C1, C2, C3, against the real station counts of this campus. We parse the `host` field —
> `c2r9s2` — and turn a seat name into a live floor map.
>
> Intra can tell you *you* are logged in. It cannot tell you the building is half empty before you
> cycle here in the rain.
>
> **Message of this page: you are not grinding alone.**

---

## 1:05 – 1:50 — Page 2: what the campus just won

> **Recent conquests.** Every Common Core project validated on this campus, newest first, with the
> mark. `szdanows`, Python Module 07, one hundred out of a hundred, fourteen hours ago. That's a
> celebration you currently only get if you happen to be standing next to the person.
>
> **Campus stats.** Average milestone across everyone not blackholed — three point six. Average
> session length. Projects passed this week. Peer evaluations. Four numbers that describe the whole
> campus, and none of them exist anywhere in Intra's UI.
>
> **The cluster residents.** Total hours on campus over the last seven days. Ninety-one hours.
> Seventy-eight. This is the leaderboard nobody asked for and everybody checks.
>
> **Message of this page: your work is seen.**

---

## 1:50 – 2:30 — Page 3: what the campus is actually working on

> **Current active projects** — every in-progress project on campus, as a share. Ten percent of
> this campus is inside `ft_printf` right now. Ten percent in `get_next_line`. If you're stuck on
> push_swap, that bar is telling you there are people three metres away stuck on the same thing.
>
> **Top evaluators** — students who gave the most peer evaluations this week. Evaluating is the
> most thankless job at 42. It is completely invisible. Now it's on a wall, at full HD, with a
> ranking.
>
> **Daily grind log** — campus-wide login hours per weekday. Three hundred twenty hours on Monday.
> Sixty on Sunday. That's the heartbeat of the building.
>
> **Message of this page: here's where the campus is, and who's carrying it.**

---

## 2:30 – 2:55 — Design language

> One design note, because it's deliberate. The whole thing is frosted glass — soft, layered
> panels floating above a textured background, rounded shapes, real depth. That's directly inspired
> by Apple's Liquid Glass.
>
> But every choice is a TV choice, not a laptop choice. No hover. No tooltips. No click targets.
> Nothing you can only reach with a mouse, because there is no mouse. Large type, high contrast,
> readable across a room. Slides morph into each other slowly instead of cutting, because it's
> 30 hertz and it runs all day. Bottom line is a marquee, top line is time, weather, and an honest
> sync stamp.

---

## 2:55 – 3:45 — Architecture and the rate limit

> Now the part that actually decided the architecture.
>
> The 42 API gives an unprivileged app **two requests per second and twelve hundred per hour**.
> A campus dashboard touches hundreds of students. Naive code burns that budget before the page
> finishes loading.
>
> So the fetcher and the renderer are two different things, and **the renderer never calls 42**.
>
> ```
> 42 API → fetcher (~40 requests) → SQLite → FastAPI + Jinja → TV
> ```
>
> Every request goes through one serialized queue with a five-hundred-millisecond floor between
> dispatches — that's the two-per-second ceiling, enforced structurally, not by hoping. On top,
> a rolling hourly window at ninety percent of twelve hundred. If we ever get a **429**, we read
> the `Retry-After` header and pause the *entire* queue, not just that call, then retry. Token
> expires, we re-auth and retry. One full campus refresh costs forty requests.
>
> The browser polls **our** server every thirty seconds and reads SQLite. Rendering this page costs
> zero API quota. And `POST /refresh` returns a **202** immediately and runs the fetch behind it —
> that's the refresh mechanism the brief asked for.

---

## 3:45 – 4:15 — What happens when things break

> Three failure questions you're going to ask.
>
> **Anonymised or half-empty student?** Every user object goes through one card builder. No login,
> it renders as "unknown". No display name, it falls back to the login. No avatar, we generate an
> SVG with their initial instead of a broken image. The panel never crashes and never invents a name.
>
> **One endpoint dies?** Every call is wrapped. That collection comes back empty, that one panel
> goes quiet, every other panel still updates.
>
> **Intra fully down?** The last good snapshot is already in SQLite, so the TV keeps showing real
> data, and the sync stamp in the corner keeps ageing and turns amber after thirty minutes — it
> tells the room it's stale instead of lying. And if Intra is down *right now*, during this pitch,
> `make demo` rebuilds every number on that screen from committed fixtures with zero network calls.

---

## 4:15 – 4:30 — Stack

> Python, FastAPI, Jinja, SQLite, httpx, vanilla JavaScript. I started on Next.js and pivoted on
> day one — the hard part was OAuth, pagination, rate limiting and caching, not the frontend.
> Thirty-nine tests, no network needed. `campus/stats` is **403** on my token, two `/graph` paths
> are **422**, so those panels don't exist. Nothing is deployed yet — Render plus MagicInfo is
> written up, not live.

---

## 4:30 – 4:45 — Honest assessment: easy vs hard next

> What's **easy** from here: anything that already sits in SQLite. Coalition scores are fetched and
> unused — that's a panel I can ship in an afternoon. Attempt counts are already computed. Pointing
> the evaluations tile at `scale_teams` properly is a metrics change, not a new fetch. Deploying
> to Render and pointing MagicInfo at the URL is the plan in the architecture doc — it's ops, not
> invention. Dropping the demo refresh from two minutes to ten is a one-line fix.
>
> What's **hard**: getting `/graph` to take the right parameters so Intra aggregates server-side —
> I hit 422 on the bare paths and that is exactly the budget escape hatch I want. Elevated scopes
> for campus stats — that's a staff decision, not a code change. True real-time without burning
> twelve hundred requests an hour. And multi-campus: one SQLite file is correct for one TV and
> wrong the moment you want ten.

---

## 4:45 – 5:00 — Close

> Last thing. Look at the bottom of Page 1.
>
> *(Let the marquee run — English then Polish.)*
>
> That line thanks the Bocal and the building staff, in both languages, every single day, on the
> biggest screen on campus. That's not my message. I put it there on behalf of every student who
> walks past a clean cluster at 4am and never says it out loud. It's from all of us.
>
> Intra shows you your own progress. **Intra-Vision shows the campus to itself.** It's running,
> it's live, and the API research and architecture docs are in the repo.
>
> Thank you.

---

# Honest assessment (written)

## What will be easy to develop?

- **Panels from data I already have.** Coalition scores are fetched and stored but unused. Retry
  counts (`occurrence + 1`) are already in the metrics payload. Wiring either onto the TV is a
  template change, not a new API path.
- **Smarter use of `scale_teams`.** I already pull filled peer-review rows. Moving "Total Peer
  Evaluation" and Top Evaluators fully onto that source is a pure-function change in
  `metrics.py` — unit-testable without spending quota.
- **Deploy to the Social Space TV.** The app is already one process + one SQLite file. Render web
  service, env secrets, cron every 10 minutes, MagicInfo web-content slot — that's the plan in
  [architecture.md](architecture.md). No new product work.
- **Safer production cadence.** The 120 s demo loop is intentional for the pitch. Bumping it to
  `FT_REFRESH_SECONDS=600` is a config change and drops the hourly cost to ~240 requests.
- **More footer / celebration copy.** The marquee vault and the validation feed are already
  structured for new messages without touching the fetch layer.

## What will be challenging?

- **Making `/graph` work.** Bare `/graph/on/.../by/day` paths returned **422** in my probe. If I
  can find the right params, Intra aggregates server-side and one refresh stops costing ~40 list
  pages. That is the real rate-limit escape hatch — and I do not have it yet.
- **Elevated / staff scopes.** `/v2/campus/:id/stats` returns **403** with
  `X-Application-Roles: None`. That is not something I can code around; it needs an Official /
  Certified app role from 42 staff.
- **True real-time.** At 2 req/s and 1200/hr, a browser that hits Intra directly will lock the
  app out. Near-real-time cache is the ceiling unless the quota goes up.
- **Multi-campus / multi-TV.** One SQLite file, one process — correct for one Social Space display,
  not for ten campuses sharing a backend.
- **Level-up milestones from history.** Snapshot history exists in the store, but a cold start has
  nothing to diff against, so that panel stays empty until the second successful refresh.
- **Untested on the real TV.** Designed for 1920×1080 @ 30 Hz and MagicInfo, never run through
  either. Motion, blur, and font loading can still surprise on the actual hardware.
- **Privacy policy if staff want names off.** Public-scope campus data is what Intra already
  shows, but turning faces off campus-wide is a product decision with staff, not a CSS toggle.

---

# Expected judge questions + answers

**Why not real-time? Why is the data minutes old?**
Twelve hundred requests an hour, and one full refresh is forty. Real-time means the browser calls
Intra, and one stuck tab in the Social Space would lock the whole app out for an hour. Cached
near-real-time is the only honest option at this quota.

**How do you guarantee you never exceed two requests per second?**
It isn't a guess — there's one worker thread and one queue, and it will not dispatch two calls less
than five hundred milliseconds apart. Nothing in the codebase can fan out in parallel, because
nothing else is allowed to hold the HTTP client.

**What happens on a 429?**
We read `Retry-After` — it handles both the seconds form and the HTTP-date form — and pause the
entire queue for that duration plus a hundred milliseconds, then retry the same call. Pausing one
call instead of the queue is the classic mistake; you just 429 again on the next one.

**Right now `make serve` refreshes every 120 seconds. Isn't forty requests every two minutes
exactly twelve hundred an hour?**
Yes. Caught it while writing the docs and it's in the limitations. That's a demo-day setting so the
screen moves while you watch. Production is the ten-minute interval, about two hundred forty an
hour, which is what the deployment plan specifies.

**What if a student is anonymised or has no avatar?**
One `_user_card` function builds every user everywhere, so the fallback is in one place: unknown
login, display name falls back to login, missing avatar becomes a generated SVG initial. Honest
caveat — no genuinely anonymised account appeared in the Warsaw data I pulled, so that path is
unit-tested, not observed live.

**Show me what happens if the API dies.**
Kill the network and hit refresh. The page still renders from SQLite, and the sync stamp starts
ageing and goes amber. Or `make demo`, which rebuilds everything from fixtures with zero requests.

**Is this deployed? Can we see it on the TV today?**
No. It runs locally on `localhost:8000`. I was told only the winning team deploys, so I wrote
the plan instead of faking a URL: Render web service, persistent disk for the SQLite file, cron
every ten minutes, MagicInfo web-content slot pointed at the HTTPS URL.

**Why FastAPI and not Next.js?**
I started on Next.js and pivoted on day one. The work here is OAuth, pagination, rate
limiting, caching, and turning raw JSON into numbers. Next.js would have meant a JS server plus
a Python-shaped fetch layer, or rewriting the whole 42 client in TypeScript. Python got the
pipeline running in hours, and the offline fixtures fell out of it.

**"Weekly passed" and "peer evaluations" are both 73. Is that a bug?**
It's a known approximation. Weekly passes counts finished projects with a mark of seventy-five or
higher; the evaluation count treats each validated project as at least one completed peer review.
Right now those sets almost perfectly overlap, so the numbers match. The real source is
`scale_teams`, which I do fetch, and moving that panel onto it is next.

**How accurate is "who's on campus"?**
It's locations with no `end_at`, which is exactly what Intra knows. It drifts high when people
forget to log out. I present it as who's logged in, not a headcount, and I don't smooth it.

**Is showing names and faces a privacy problem?**
Everything on screen comes from the 42 API using a public-scope token, campus-scoped to Warsaw —
the same login, display name, and avatar every student already shows on Intra. No email, no phone;
Intra returns `phone` as the literal string "hidden" anyway. If staff want it anonymous, the
aggregate panels already work without a single name.

**How do you know the numbers are right?**
The metric layer is pure functions with no I/O, so it's all unit-tested — thirty-nine tests, no
network, no credentials. And I keep live fixtures in the repo, so I can re-run the exact campus
data through the pipeline and check the output.

**Did you reuse pre-built boilerplate?**
There was an abandoned Next.js scaffold at the start; it's gone from the repo. The FastAPI app,
the 42 client, the metrics, the store and the TV template were all written during the event, and
the git history shows the increments.

**How much does this cost to run?**
One small web service. SQLite means no database server, no bill. It's a single file on disk, which
is correct for exactly one TV — and a real limitation the moment you want ten campuses.

**What will be easy / what will be challenging?**
See the Honest assessment section above. Short version: easy = anything already in SQLite or the
deploy plan; hard = `/graph` params, elevated scopes, real-time, multi-campus, and the real TV.
