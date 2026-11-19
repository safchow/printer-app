import * as React from "react";
import { ChevronLeft, ChevronRight, Pause, Play } from "lucide-react";
import { frameUrl } from "@/lib/files";
import { clock } from "@/lib/format";
import type { Timelapse } from "@/lib/types";
import { cn } from "@/lib/utils";

const SPEEDS = [10, 24, 60];
const PRELOAD = 40; // frames fetched ahead of the one shown

/**
 * Plays a print's time-lapse: one saved camera frame per layer, shown in turn.
 * Nothing is encoded; frames are plain JPEGs the browser caches.
 */
export function TimelapsePlayer({ jobId, tl, className }: { jobId: number; tl: Timelapse; className?: string }) {
  const frames = tl.frames;
  const last = frames.length - 1;
  const [i, setI] = React.useState(last);
  const [playing, setPlaying] = React.useState(false);
  const [fps, setFps] = React.useState(24);
  const cache = React.useRef(new Map<number, HTMLImageElement>());

  // Keep the next frames loading so playback doesn't stall.
  React.useEffect(() => {
    const map = cache.current;
    for (let k = i; k <= Math.min(last, i + PRELOAD); k++) {
      if (!map.has(k)) {
        const img = new Image();
        img.src = frameUrl(jobId, frames[k].n);
        map.set(k, img);
      }
    }
  }, [i, last, jobId, frames]);

  React.useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setI((cur) => {
        if (cur >= last) {
          setPlaying(false);
          return cur;
        }
        const next = cache.current.get(cur + 1);
        return next && !next.complete ? cur : cur + 1; // wait for a frame that's still loading
      });
    }, Math.round(1000 / fps));
    return () => window.clearInterval(timer);
  }, [playing, fps, last]);

  if (!frames.length) return null;
  const f = frames[i];
  const prev = i > 0 ? frames[i - 1] : null;
  const missingBefore = prev && f.layer != null && prev.layer != null ? f.layer - prev.layer - 1 : 0;
  const total = tl.total_layers;
  const gapMarks = frames
    .map((fr, k) => (k > 0 && fr.layer != null && frames[k - 1].layer != null && fr.layer - (frames[k - 1].layer as number) > 1 ? k : -1))
    .filter((k) => k > 0);

  const toggle = () => {
    if (playing) setPlaying(false);
    else {
      if (i >= last) setI(0);
      setPlaying(true);
    }
  };
  const step = (d: number) => {
    setPlaying(false);
    setI((cur) => Math.max(0, Math.min(last, cur + d)));
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowLeft") step(-1);
    else if (e.key === "ArrowRight") step(1);
    else if (e.key === " ") toggle();
    else return;
    e.preventDefault();
  };

  return (
    <div
      className={cn("relative flex items-center justify-center overflow-hidden bg-black outline-none", className)}
      tabIndex={0}
      onKeyDown={onKey}
      aria-label="Time-lapse. Arrow keys step one frame, space plays."
    >
      <img src={frameUrl(jobId, f.n)} alt={f.layer != null ? `Camera at layer ${f.layer}` : `Frame ${i + 1}`} className="max-h-full max-w-full object-contain" />

      <div className="absolute top-3 left-3 flex flex-wrap gap-2 font-mono text-xs text-white">
        <span className="rounded-md bg-black/65 px-2 py-1">
          {f.layer != null ? `Layer ${f.layer}${total ? ` / ${total}` : ""}` : `Frame ${i + 1} / ${frames.length}`}
        </span>
        {f.t && <span className="rounded-md bg-black/65 px-2 py-1">{clock(f.t)}</span>}
        {tl.recording && <span className="rounded-md bg-red-600/90 px-2 py-1">Recording</span>}
      </div>
      {missingBefore > 0 && !playing && (
        <div className="absolute top-12 left-1/2 -translate-x-1/2 rounded-lg bg-amber-950/90 px-3 py-1.5 text-[13px] whitespace-nowrap text-amber-300">
          {missingBefore === 1 ? `No frame for layer ${f.layer! - 1}` : `No frames for layers ${prev!.layer! + 1} to ${f.layer! - 1}`}
        </div>
      )}

      <div className="absolute inset-x-0 bottom-0 flex flex-col gap-2 bg-gradient-to-b from-transparent to-black/75 px-4 pt-7 pb-3.5 text-white">
        <div className="relative">
          {last > 0 &&
            gapMarks.map((k) => (
              <span
                key={k}
                className="absolute -top-1.5 h-1 w-1.5 rounded-sm bg-amber-500"
                style={{ left: `calc(${((k - 0.5) / last) * 100}% - 3px)` }}
                aria-hidden="true"
              />
            ))}
          <input
            type="range"
            min={0}
            max={Math.max(0, last)}
            step={1}
            value={i}
            onChange={(e) => {
              setPlaying(false);
              setI(Number(e.target.value));
            }}
            aria-label="Scrub through the time-lapse"
            className="h-5 w-full accent-white"
          />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-1.5">
            <button type="button" onClick={() => step(-1)} aria-label="Previous frame" className="inline-flex size-10 items-center justify-center rounded-lg bg-white/15 hover:bg-white/25">
              <ChevronLeft className="size-5" aria-hidden="true" />
            </button>
            <button type="button" onClick={toggle} aria-label={playing ? "Pause" : "Play"} className="inline-flex h-10 w-12 items-center justify-center rounded-lg bg-white text-black hover:bg-white/90">
              {playing ? <Pause className="size-4" aria-hidden="true" /> : <Play className="size-4" aria-hidden="true" />}
            </button>
            <button type="button" onClick={() => step(1)} aria-label="Next frame" className="inline-flex size-10 items-center justify-center rounded-lg bg-white/15 hover:bg-white/25">
              <ChevronRight className="size-5" aria-hidden="true" />
            </button>
            <span className="ml-2 font-mono text-xs opacity-85">
              {frames.length} frames{tl.gaps.length ? ` · ${tl.gaps.length} gap${tl.gaps.length === 1 ? "" : "s"}` : ""}
            </span>
          </div>
          <div role="group" aria-label="Speed" className="inline-flex gap-0.5 rounded-lg bg-white/15 p-0.5">
            {SPEEDS.map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setFps(v)}
                aria-pressed={fps === v}
                className={cn("h-9 min-w-[60px] rounded-md px-2 text-xs font-medium", fps === v ? "bg-white text-black" : "text-white hover:bg-white/15")}
              >
                {v} fps
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
