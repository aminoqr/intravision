// File: components/PageFaction.tsx
export default function PageFaction() {
  return (
    <section className="flex h-full w-full items-center justify-center bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950 px-16">
      <div className="flex max-w-5xl flex-col items-center text-center">
        <p className="mb-6 text-2xl font-bold tracking-[0.4em] text-sky-300 uppercase">
          Page 3 Canvas
        </p>
        <h2 className="text-8xl font-black tracking-tight text-white drop-shadow-[0_2px_24px_rgba(56,189,248,0.25)]">
          FACTION
        </h2>
        <p className="mt-8 max-w-3xl text-3xl font-semibold leading-snug text-slate-200">
          Community overview — coalition standings and campus-wide momentum on
          one canvas.
        </p>
        <div className="mt-12 rounded-2xl border border-slate-700 bg-slate-900/80 px-10 py-6">
          <p className="text-xl font-bold tracking-wide text-slate-300">
            Placeholder shell · rotates every 15 seconds · high-contrast TV mode
          </p>
        </div>
      </div>
    </section>
  );
}
