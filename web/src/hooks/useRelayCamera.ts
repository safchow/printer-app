import * as React from "react";
import type { CameraState } from "@/hooks/useCamera";

// Codecs go2rtc can send as fragmented MP4; we offer the ones this browser plays.
// Main and Baseline are listed too: the K1C sends Main 3.0 (avc1.4D401E).
const CODECS = ["avc1.640029", "avc1.64002A", "avc1.640033", "avc1.4D401E", "avc1.42E01E", "hvc1.1.6.L153.B0"];
const NO_VIDEO_MS = 12000;
const RETRIES = 2; // live attempts before falling back to stills
const RETRY_MS = [1500, 4000];
const STILL_EVERY_MS = 2000;
const STILL_FAILURES = 3; // stills that fail in a row before giving up
const BEHIND_S = 3; // jump to the live edge when playback is further behind than this
const KEEP_S = 4; // seconds kept behind the playhead

export type CameraMode = "video" | "stills";

type MSECtor = typeof MediaSource;

function mediaSource(): { Ctor: MSECtor; managed: boolean } | null {
  const w = window as unknown as { ManagedMediaSource?: MSECtor; MediaSource?: MSECtor };
  if (w.ManagedMediaSource) return { Ctor: w.ManagedMediaSource, managed: true }; // iPhone Safari
  if (w.MediaSource) return { Ctor: w.MediaSource, managed: false };
  return null;
}

const errName = (e: unknown) => (e instanceof Error || e instanceof DOMException ? e.name : "error");

/**
 * Live camera through the server's relay (go2rtc). The server holds the one
 * connection to the printer; this page gets fragmented MP4 over a WebSocket
 * and plays it with Media Source Extensions.
 *
 * Starts on its own while `enabled` and the tab is visible, and stops when the
 * tab is hidden. A failed stream is retried twice by itself; after that the
 * page shows a still from the camera every 2 seconds instead, until the
 * person taps to try live video again.
 */
export function useRelayCamera(printerId: string, enabled: boolean) {
  const [state, setState] = React.useState<CameraState>("idle");
  const [mode, setMode] = React.useState<CameraMode>("video");
  const [error, setError] = React.useState<string | null>(null);
  const [visible, setVisible] = React.useState(() => document.visibilityState === "visible");
  const [attempt, setAttempt] = React.useState(0);
  const [still, setStill] = React.useState<string | null>(null);
  const retries = React.useRef(0);
  const videoRef = React.useRef<HTMLVideoElement>(null);

  React.useEffect(() => {
    const onVis = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  const run = enabled && visible && error === null;

  // A failed live attempt: retry, then fall back to stills.
  const failed = React.useCallback((message: string, fallback = true) => {
    if (retries.current < RETRIES) {
      const wait = RETRY_MS[retries.current] ?? 4000;
      retries.current += 1;
      setState("connecting");
      window.setTimeout(() => setAttempt((n) => n + 1), wait);
      return;
    }
    if (fallback) {
      console.warn(`camera: live video failed (${message}); showing stills`);
      setMode("stills");
      setState("connecting");
      return;
    }
    setError(message);
    setState("error");
  }, []);

  // --- live video ---------------------------------------------------------------
  React.useEffect(() => {
    const video = videoRef.current;
    if (!run || mode !== "video" || !video) return;
    const mse = mediaSource();
    if (!mse) {
      setMode("stills");
      return;
    }

    let closed = false;
    let live = false;
    let objectUrl: string | null = null;
    let sb: SourceBuffer | null = null;
    const queue: ArrayBuffer[] = [];
    setState("connecting");

    const fail = (message: string, fallback = true) => {
      if (closed) return;
      cleanup();
      failed(message, fallback);
    };

    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/printers/${encodeURIComponent(printerId)}/camera/ws`);
    ws.binaryType = "arraybuffer";

    // Drop what's already been played, so the buffer stays small.
    const trim = (): boolean => {
      if (!sb || sb.updating || sb.buffered.length === 0) return false;
      const start = sb.buffered.start(0);
      const upTo = video.currentTime - KEEP_S;
      if (upTo - start < 2) return false; // remove in bigger steps, not after every frame
      sb.remove(start, upTo);
      return true;
    };

    const pump = () => {
      if (!sb || sb.updating || queue.length === 0) return;
      try {
        sb.appendBuffer(queue[0]);
        queue.shift();
      } catch (e) {
        // Full: make room and try this chunk again once the removal is done.
        if (errName(e) === "QuotaExceededError" && trim()) return;
        if (errName(e) === "QuotaExceededError") {
          queue.shift(); // nothing to remove yet: skip a frame rather than stop
          return;
        }
        const code = video.error ? `, media error ${video.error.code}` : "";
        fail(`The camera stream couldn't be played (${errName(e)}${code}).`);
      }
    };

    const catchUp = () => {
      if (!sb || video.buffered.length === 0) return;
      const end = video.buffered.end(video.buffered.length - 1);
      if (end - video.currentTime > BEHIND_S) video.currentTime = end - 0.5;
    };

    const onPlaying = () => {
      if (!live) {
        live = true;
        retries.current = 0;
        setState("live");
      }
    };
    const onError = () => {
      const err = video.error;
      if (err) fail(`The camera stream couldn't be played (media error ${err.code}${err.message ? `: ${err.message}` : ""}).`);
    };
    video.addEventListener("playing", onPlaying);
    video.addEventListener("error", onError);
    const noVideo = window.setTimeout(() => {
      if (!live) fail("No video arrived from the camera.");
    }, NO_VIDEO_MS);

    ws.onopen = () => {
      const codecs = CODECS.filter((c) => mse.Ctor.isTypeSupported(`video/mp4; codecs="${c}"`));
      ws.send(JSON.stringify({ type: "mse", value: codecs.join(",") }));
    };

    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") {
        queue.push(ev.data as ArrayBuffer);
        pump();
        return;
      }
      const msg = JSON.parse(ev.data) as { type: string; value: string };
      if (msg.type === "error") {
        fail(`The camera relay said: ${msg.value}`, msg.value !== "printer is offline");
      } else if (msg.type === "mse") {
        const ms = new mse.Ctor();
        ms.addEventListener(
          "sourceopen",
          () => {
            if (closed) return;
            try {
              sb = ms.addSourceBuffer(msg.value);
              sb.mode = "segments";
              sb.addEventListener("updateend", () => {
                if (closed) return;
                catchUp();
                if (!trim()) pump();
              });
              pump();
            } catch (e) {
              fail(`This browser can't play the camera's video format (${msg.value}, ${errName(e)}).`);
            }
          },
          { once: true },
        );
        if (mse.managed) {
          video.disableRemotePlayback = true;
          video.srcObject = ms as unknown as MediaStream;
        } else {
          objectUrl = URL.createObjectURL(ms);
          video.src = objectUrl;
        }
        video.play().catch(() => {});
      }
    };

    ws.onclose = () => {
      if (closed) return;
      fail(live ? "The camera stream stopped." : "The camera relay closed the connection.");
    };

    const cleanup = () => {
      if (closed) return;
      closed = true;
      window.clearTimeout(noVideo);
      video.removeEventListener("playing", onPlaying);
      video.removeEventListener("error", onError);
      ws.onclose = null;
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.close();
      video.pause();
      video.removeAttribute("src");
      video.srcObject = null;
      video.load();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };

    return () => {
      cleanup();
      setState((s) => (s === "error" ? s : "idle"));
    };
  }, [run, mode, printerId, attempt, failed]);

  // --- stills fallback ------------------------------------------------------------
  React.useEffect(() => {
    if (!run || mode !== "stills") return;
    setState("connecting");
    let stopped = false;
    let misses = 0;
    let timer = 0;
    let shown: string | null = null;
    const url = `/api/printers/${encodeURIComponent(printerId)}/camera/still.jpg`;

    const tick = async () => {
      try {
        const r = await fetch(url, { cache: "no-store" });
        if (!r.ok) throw new Error(String(r.status));
        const next = URL.createObjectURL(await r.blob());
        if (stopped) return URL.revokeObjectURL(next);
        if (shown) URL.revokeObjectURL(shown);
        shown = next;
        misses = 0;
        setStill(next);
        setState("live");
      } catch {
        if (stopped) return;
        misses += 1;
        if (misses >= STILL_FAILURES) {
          setError("The camera isn't sending pictures.");
          setState("error");
          return;
        }
      }
      if (!stopped) timer = window.setTimeout(tick, STILL_EVERY_MS);
    };
    tick();

    return () => {
      stopped = true;
      window.clearTimeout(timer);
      if (shown) URL.revokeObjectURL(shown);
      setStill(null);
      setState((s) => (s === "error" ? s : "idle"));
    };
  }, [run, mode, printerId]);

  /** Start over with live video. */
  const retry = React.useCallback(() => {
    retries.current = 0;
    setError(null);
    setMode("video");
    setAttempt((n) => n + 1);
  }, []);

  return { state, mode, still, error, videoRef, retry };
}
