// File: app/page.tsx
"use client";

import { useEffect, useState } from "react";
import PagePulse from "@/components/PagePulse";
import PageFame from "@/components/PageFame";
import PageFaction from "@/components/PageFaction";

const VIEWS = ["pulse", "fame", "faction"] as const;

type ViewKey = (typeof VIEWS)[number];

const ROTATION_MS = 15_000;

export default function HomePage() {
  const [activeView, setActiveView] = useState<ViewKey>("pulse");

  useEffect(() => {
    const timer = window.setInterval(() => {
      setActiveView((current) => {
        const index = VIEWS.indexOf(current);
        const nextIndex = (index + 1) % VIEWS.length;
        return VIEWS[nextIndex];
      });
    }, ROTATION_MS);

    return () => {
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden">
      <div className="absolute top-6 right-10 z-20 flex items-center gap-3 rounded-full border border-slate-700 bg-slate-900/90 px-5 py-2">
        {VIEWS.map((view) => {
          const isActive = view === activeView;
          return (
            <span
              key={view}
              className={
                isActive
                  ? "text-base font-extrabold tracking-[0.25em] text-emerald-400 uppercase"
                  : "text-base font-semibold tracking-[0.25em] text-slate-500 uppercase"
              }
            >
              {view}
            </span>
          );
        })}
      </div>

      <div
        key={activeView}
        className="animate-canvasFade flex h-full w-full min-h-0 flex-1 overflow-hidden"
      >
        {activeView === "pulse" ? <PagePulse /> : null}
        {activeView === "fame" ? <PageFame /> : null}
        {activeView === "faction" ? <PageFaction /> : null}
      </div>
    </div>
  );
}
