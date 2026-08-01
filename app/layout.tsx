// File: app/layout.tsx
import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "42 Warsaw Hacks - Intra-Vision",
  description:
    "Passive Social Space television dashboard for 42 Warsaw Hacks (1920×1080 @ 30Hz).",
  robots: {
    index: false,
    follow: false,
  },
};

export const viewport: Viewport = {
  width: 1920,
  height: 1080,
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

const TICKER_ITEMS = [
  "INTRA-VISION ONLINE",
  "WARSAW SOCIAL SPACE // 1920×1080 @ 30Hz",
  "CAROUSEL: PULSE → FAME → FACTION // 15s",
  "PEER PROGRESS VISIBLE",
  "RECENT VALIDATIONS ON ROTATION",
  "COALITION STANDINGS LIVE FROM CACHE",
  "NO HOVER REQUIRED — PASSIVE DISPLAY MODE",
  "API FETCH DECOUPLED FROM RENDER — ZERO QUOTA ON PAINT",
] as const;

function TickerSegment() {
  return (
    <>
      {TICKER_ITEMS.map((item) => (
        <span
          key={item}
          className="mx-10 inline-flex items-center gap-4 text-xl font-semibold tracking-wide text-slate-100"
        >
          <span className="text-emerald-400" aria-hidden="true">
            ◆
          </span>
          <span>{item}</span>
        </span>
      ))}
    </>
  );
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="h-screen w-screen overflow-hidden bg-slate-950 font-display text-white antialiased select-none">
        <div className="flex h-screen w-screen flex-col overflow-hidden bg-slate-950 text-white">
          <header className="flex h-24 shrink-0 items-center justify-between gap-8 border-b border-slate-800 bg-slate-950 px-10">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-bold tracking-[0.35em] text-slate-400 uppercase">
                42 WARSAW HACKS
              </p>
              <h1 className="truncate text-4xl font-black tracking-tight text-white">
                42 WARSAW INTRA-VISION
              </h1>
            </div>

            <div className="flex shrink-0 items-stretch overflow-hidden rounded-full bg-white shadow-[0_0_0_1px_rgba(15,23,42,0.08)]">
              <div className="flex items-center gap-3 border-r border-slate-200 bg-slate-100 px-6 py-3">
                <span
                  className="text-2xl leading-none text-emerald-500"
                  aria-hidden="true"
                >
                  🟢
                </span>
                <div className="leading-tight">
                  <p className="text-xs font-bold tracking-[0.2em] text-slate-500 uppercase">
                    API Cache Status
                  </p>
                  <p className="text-lg font-extrabold tracking-wide text-slate-900">
                    INTRA LINK: SYNCED // 45s
                  </p>
                </div>
              </div>

              <div className="flex items-center px-7 py-3">
                <p className="text-2xl font-extrabold tracking-wide text-slate-900 tabular-nums">
                  20°C <span className="mx-2 font-semibold text-slate-400">|</span>{" "}
                  Warsaw <span className="mx-2 font-semibold text-slate-400">|</span>{" "}
                  14:00
                </p>
              </div>
            </div>
          </header>

          <main className="relative min-h-0 flex-1 overflow-hidden bg-slate-950">
            {children}
          </main>

          <footer className="relative h-14 shrink-0 overflow-hidden border-t border-slate-800 bg-slate-900">
            <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-24 bg-gradient-to-r from-slate-900 to-transparent" />
            <div className="pointer-events-none absolute inset-y-0 right-0 z-10 w-24 bg-gradient-to-l from-slate-900 to-transparent" />
            <div className="flex h-full items-center overflow-hidden">
              <div className="ticker-track whitespace-nowrap" aria-hidden="true">
                <TickerSegment />
                <TickerSegment />
              </div>
            </div>
            <span className="sr-only">
              Live system alerts ticker for Intra-Vision dashboard.
            </span>
          </footer>
        </div>
      </body>
    </html>
  );
}
