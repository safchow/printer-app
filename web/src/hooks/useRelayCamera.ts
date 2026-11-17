import * as React from "react";
import type { CameraState } from "@/hooks/useCamera";

// Codecs go2rtc can send as fragmented MP4; we offer the ones this browser plays.
const CODECS = ["avc1.640029", "avc1.64002A", "avc1.640033", "hvc1.1.6.L153.B0"];
const NO_VIDEO_MS = 20000;

type MSECtor = typeof MediaSource;

function mediaSource(): { Ctor: MSECtor; managed: boolean } | null {
  const w = window as unknown as { ManagedMediaSource?: MSECtor; MediaSource?: MSECtor };
  if (w.ManagedMediaSource) return { Ctor: w.ManagedMediaSource, managed: true }; // iPhone Safari
  if (w.MediaSource) return { Ctor: w.MediaSource, managed: false };
  return null;
}

/**
 * Live camera through the server's relay (go2rtc). The server holds the one
 * connection to the printer; this page gets fragmented MP4 over a WebSocket
 * and plays it with Media Source Extensions.
 *
 * Starts on its own while `enabled` and the tab is visible, stops when the tab
 * is hidden, and never retries an error by itself: the person taps Try again.
 */
export function useRelayCamera(printerId: string, enabled: boolean) {
  const [state, setState] = React.useState<CameraState>("idle");
  const [error, setError] = React.useState<string | null>(null);
  const [visible, setVisible] = React.useState(() => document.visibilityState === "visible");
  const [stopped, setStopped] = React.useState(false);
  const [attempt, setAttempt] = React.useState(0);
  const videoRef = React.useRef<HTMLVideoElement>(null);

  React.useEffect(() => {
    const onVis = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  const run = enabled && visible && !stopped && error === null;

  React.useEffect(() => {
    const video = videoRef.current;
    if (!run || !video) return;
    const mse = mediaSource();
    if (!mse) {
      setError("This browser can't play the camera stream.");
      setState("error");
      return;
    }

    let closed = false;
    let live = false;
    let objectUrl: string | null = null;
    let sb: SourceBuffer | null = null;
    const queue: ArrayBuffer[] = [];
    setState("connecting");

    const fail = (message: string) => {
      if (closed) return;
      setError(message);
      setState("error");
      cleanup();
    };

    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/printers/${encodeURIComponent(printerId)}/camera/ws`);
    ws.binaryType = "arraybuffer";

    const pump = () => {
      if (!sb || sb.updating || queue.length === 0) return;
      try {
        sb.appendBuffer(queue.shift()!);
      } catch {
        fail("The camera stream couldn't be played.");
      }
    };

    const trimAndCatchUp = () => {
      if (!sb || sb.updating || video.buffered.length === 0) return;
      const start = video.buffered.start(0);
      const end = video.buffered.end(video.buffered.length - 1);
      // Stay near the live edge, and keep only a few seconds buffered.
      if (end - video.currentTime > 1.5) video.currentTime = end - 0.3;
      if (end - start > 12) sb.remove(start, end - 6);
    };

    const onPlaying = () => {
      if (!live) {
        live = true;
        setState("live");
      }
    };
    video.addEventListener("playing", onPlaying);
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
        fail(`The camera relay said: ${msg.value}`);
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
                trimAndCatchUp();
                pump();
              });
              pump();
            } catch {
              fail(`This browser can't play the camera's video format (${msg.value}).`);
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
      if (live) {
        setError("The camera stream stopped.");
        setState("error");
        cleanup();
      } else {
        fail("The camera relay closed the connection.");
      }
    };

    const cleanup = () => {
      if (closed) return;
      closed = true;
      window.clearTimeout(noVideo);
      video.removeEventListener("playing", onPlaying);
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
  }, [run, printerId, attempt]);

  const retry = React.useCallback(() => {
    setError(null);
    setStopped(false);
    setAttempt((n) => n + 1);
  }, []);
  const stop = React.useCallback(() => setStopped(true), []);

  return { state: stopped ? ("idle" as const) : state, error, stopped, videoRef, retry, stop };
}
