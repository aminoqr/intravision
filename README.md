<!-- File: README.md -->

# 42 Warsaw Hacks - Intra-Vision

Passive Social Space television dashboard for **42 Warsaw Hacks** (1–2 August 2026).
Built for a fixed **1920×1080 @ 30Hz** widescreen display — large type, high contrast,
no hover-dependent interactions.

## Stack

- **Next.js** (App Router)
- **TypeScript**
- **Tailwind CSS**

Server-side Intra API access only. App UID / SECRET never use the `NEXT_PUBLIC_` prefix.

## Screens (carousel)

| Key | Component | Role |
|-----|-----------|------|
| `pulse` | `components/PagePulse.tsx` | Campus pulse / peer progress |
| `fame` | `components/PageFame.tsx` | Recent validations / celebration |
| `faction` | `components/PageFaction.tsx` | Coalition / community overview |

The central canvas rotates every **15 seconds**. Header status + bottom marquee stay fixed.

## Install

```bash
npm install
```

Copy environment values into `.env.local` (gitignored). Example shape:

```bash
# Server-only secrets — NEVER prefix with NEXT_PUBLIC_
FORTYTWO_APP_UID=...
FORTYTWO_APP_SECRET=...

# Safe to expose to the client (ids only)
NEXT_PUBLIC_CAMPUS_ID=67
NEXT_PUBLIC_CURSUS_ID=21
```

Resolve Warsaw campus / Common Core cursus ids from Intra (Python helper):

```bash
make resolve-ids
```

## Develop

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Prefer a 1920×1080 browser window
or the TV’s full-screen mode when validating layout.

## Production build

```bash
npm run build
npm run start
```

## Agent rules

See `.cursorrules` and `CURSOR.md` for generation contracts (full code only, path
prefixes, secret handling, TV typography).
