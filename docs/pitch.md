<!-- File: docs/pitch.md -->

# Pitch outline — Intra-Vision (≤5 minutes)

Demo from **`http://localhost:8000`** at 1920×1080. Have `make demo` ready if Intra is down.
Backup: screenshots in `docs/screenshots/`.

## 0:00–0:30 — Problem

Social Space TV should celebrate **other students’ Common Core progress** — not another admin
panel. Viewers are across the room, no mouse. We built a passive FullHD dashboard on the 42 API.

## 0:30–2:00 — Live demo

1. Open the dashboard. Let the carousel rotate:
   - Recently validated (names, projects, marks)
   - Campus pulse (on campus / validated this week / median level)
   - Coalitions + level distribution
2. Point at the **updated … ago** stamp — honest freshness.
3. Optional: `curl -X POST localhost:8000/refresh` — brief’s refresh mechanism (202).
4. If live API fails: switch narrative to fixtures / last cache — still a full screen.

## 2:00–3:00 — Architecture (60 seconds)

```
42 API → fetcher (~11 req / 10 min) → SQLite → FastAPI/Jinja → TV or laptop browser
```

Renderer never calls Intra. Browser only hits `/api/metrics`. Rate limit: 2/s, 1200/hr — we stay
around 5% of the hourly budget. Stack pivot: FastAPI for speed + offline fixtures after starting
from a Next-oriented plan.

Post-win (not required today): same app on **Render**, MagicInfo web slot on the Social Space TV.

## 3:00–4:00 — Honest limitations (scoring opportunity)

- `/campus/:id/stats` → **403** on public token — we didn’t fake a panel we can’t feed  
- Some `/graph` paths → **422** — used paginated lists + local metrics instead  
- Data is minutes stale by design; stamp shows it  
- Level-ups need history; cold start is empty  
- Deploy not in scope for judging; localhost demo is intentional  

## 4:00–4:45 — Easy vs hard next

**Easy:** more celebration cards, Tailwind polish, cron on a Pi/Render.  
**Hard:** deeper `/graph` params, elevated scopes, multi-campus, true real-time without blowing
1200/hr.

## 4:45–5:00 — Close

Working PoC: ≥2 live endpoints, processed metrics on a TV-shaped page, refresh, offline fallback,
API research + architecture docs in the repo. Disclose any pre-built scaffolding if asked.
