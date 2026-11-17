import * as React from "react";

export type CameraState = "idle" | "connecting" | "live" | "error";

/** Waits for ICE gathering so the one-shot handshake carries every candidate. */
function gatheringDone(pc: RTCPeerConnection, ms = 5000): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const t = window.setTimeout(resolve, ms);
    pc.addEventListener("icegatheringstatechange", () => {
      if (pc.iceGatheringState === "complete") {
        window.clearTimeout(t);
        resolve();
      }
    });
  });
}

/**
 * Live camera over WebRTC. Starts only when asked, stops when the tab is
 * hidden or the page closes. The server only relays the handshake; video
 * comes straight from the printer.
 */
export function useCamera(printerId: string) {
  const [state, setState] = React.useState<CameraState>("idle");
  const [error, setError] = React.useState<string | null>(null);
  const [stream, setStream] = React.useState<MediaStream | null>(null);
  const pcRef = React.useRef<RTCPeerConnection | null>(null);

  const stop = React.useCallback(() => {
    pcRef.current?.close();
    pcRef.current = null;
    setStream(null);
    setState((s) => (s === "error" ? s : "idle"));
  }, []);

  const start = React.useCallback(async () => {
    pcRef.current?.close();
    setError(null);
    setState("connecting");
    const pc = new RTCPeerConnection({ iceServers: [] });
    pcRef.current = pc;
    // The K1C only sends video when the offer is sendrecv, exactly as its own
    // page at :8000 does it. Nothing is ever sent from this side.
    pc.addTransceiver("video", { direction: "sendrecv" });
    pc.ontrack = (e) => {
      setStream(e.streams[0] ?? new MediaStream([e.track]));
      setState("live");
    };
    pc.onconnectionstatechange = () => {
      if (pcRef.current !== pc) return;
      if (pc.connectionState === "failed") {
        setError("The video connection failed. Check that this device can reach the printer's address over the VPN.");
        setState("error");
        pc.close();
      } else if (pc.connectionState === "disconnected" || pc.connectionState === "closed") {
        setStream(null);
        setState((s) => (s === "live" ? "idle" : s));
      }
    };
    try {
      await pc.setLocalDescription(await pc.createOffer());
      await gatheringDone(pc);
      const local = pc.localDescription!;
      const r = await fetch(`/api/printers/${encodeURIComponent(printerId)}/camera/offer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: local.type, sdp: local.sdp }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `camera request failed (${r.status})`);
      if (pcRef.current !== pc) return;
      try {
        await pc.setRemoteDescription(body);
      } catch (e) {
        throw new Error(`The camera's answer couldn't be used (${e instanceof Error ? e.message : e}).`);
      }
      // If no video arrives soon, say so instead of spinning forever.
      window.setTimeout(() => {
        if (pcRef.current === pc && pc.connectionState !== "connected") {
          setError("No video arrived from the camera.");
          setState("error");
          pc.close();
          pcRef.current = null;
        }
      }, 15000);
    } catch (e) {
      if (pcRef.current !== pc) return;
      pc.close();
      pcRef.current = null;
      setError(e instanceof Error ? e.message : String(e));
      setState("error");
    }
  }, [printerId]);

  React.useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden") stop();
    };
    document.addEventListener("visibilitychange", onHide);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      pcRef.current?.close();
      pcRef.current = null;
    };
  }, [stop]);

  return { state, error, stream, start, stop };
}
