import * as React from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { parseGcode, printedCount, type Toolpath } from "@/lib/gcode";
import type { Printer } from "@/lib/types";
import { cn } from "@/lib/utils";

type Info =
  | { state: "off" | "none"; filename?: string }
  | { state: "fetching"; filename: string }
  | { state: "ready"; filename: string; size: number }
  | { state: "failed"; filename: string; detail: string };

/**
 * The current print's G-code: the server fetches it once per print; this asks
 * the server (never the printer) whether it's ready, then loads and parses it.
 */
function useToolpath(p: Printer) {
  const [info, setInfo] = React.useState<Info | null>(null);
  const [tp, setTp] = React.useState<{ key: string; size: number; path: Toolpath } | null>(null);
  const active = p.state === "printing" || p.state === "paused";

  React.useEffect(() => {
    if (!p.filename) {
      setInfo({ state: "none" });
      return;
    }
    let stop = false;
    let timer: number | undefined;
    const check = () => {
      fetch(`/api/printers/${encodeURIComponent(p.id)}/gcode/info`)
        .then((r) => (r.ok ? r.json() : null))
        .then((i: Info | null) => {
          if (stop || !i) return;
          setInfo(i);
          // While the server is still fetching (or a print just started), look again shortly.
          if (active && (i.state === "fetching" || i.state === "none")) timer = window.setTimeout(check, 4000);
        })
        .catch(() => {});
    };
    check();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [p.id, p.filename, active]);

  const readyKey = info?.state === "ready" ? `${info.filename}:${info.size}` : null;
  React.useEffect(() => {
    if (!readyKey || tp?.key === readyKey || info?.state !== "ready") return;
    let stop = false;
    const size = info.size;
    fetch(`/api/printers/${encodeURIComponent(p.id)}/gcode`)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((text) => {
        if (stop) return;
        setTp({ key: readyKey, size, path: parseGcode(text) });
      })
      .catch(() => {});
    return () => {
      stop = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readyKey, p.id]);

  return { info, toolpath: tp && tp.key === readyKey ? tp : null };
}

function cssColor(name: string, fallback: string): THREE.Color {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const c = new THREE.Color(fallback);
  if (!v) return c;
  try {
    // Let the browser resolve oklch()/hsl() etc. to rgb.
    const probe = document.createElement("span");
    probe.style.color = v;
    document.body.appendChild(probe);
    const rgb = getComputedStyle(probe).color;
    probe.remove();
    const m = rgb.match(/[\d.]+/g);
    if (m && m.length >= 3) c.setRGB(+m[0] / 255, +m[1] / 255, +m[2] / 255, THREE.SRGBColorSpace);
  } catch {
    /* keep the fallback */
  }
  return c;
}

function Scene({ tp, size, progress }: { tp: Toolpath; size: number; progress: number }) {
  const host = React.useRef<HTMLDivElement>(null);
  const api = React.useRef<{ setDone: (k: number) => void } | null>(null);

  React.useEffect(() => {
    const el = host.current;
    if (!el) return;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    renderer.domElement.style.display = "block";

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.5, 5000);
    camera.up.set(0, 0, 1); // printers are Z-up

    const attr = new THREE.BufferAttribute(tp.positions, 3);
    const doneGeo = new THREE.BufferGeometry();
    doneGeo.setAttribute("position", attr);
    const todoGeo = new THREE.BufferGeometry();
    todoGeo.setAttribute("position", attr);
    const brand = cssColor("--brand", "#2563eb");
    const muted = cssColor("--muted-foreground", "#71717a");
    const done = new THREE.LineSegments(doneGeo, new THREE.LineBasicMaterial({ color: brand }));
    const todo = new THREE.LineSegments(
      todoGeo,
      new THREE.LineBasicMaterial({ color: muted, transparent: true, opacity: 0.18, depthWrite: false }),
    );
    scene.add(done, todo);

    const nozzle = new THREE.Mesh(new THREE.SphereGeometry(1, 12, 12), new THREE.MeshBasicMaterial({ color: brand }));
    scene.add(nozzle);

    const [x0, y0, z0] = tp.min;
    const [x1, y1, z1] = tp.max;
    const center = new THREE.Vector3((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
    const radius = Math.max(10, Math.hypot(x1 - x0, y1 - y0, z1 - z0) / 2);
    nozzle.scale.setScalar(Math.max(0.6, radius / 60));

    // A floor grid under the part.
    const gridSize = Math.ceil((Math.max(x1 - x0, y1 - y0) * 1.4) / 10) * 10 || 50;
    const grid = new THREE.GridHelper(gridSize, gridSize / 10, muted, muted);
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.15;
    grid.rotation.x = Math.PI / 2;
    grid.position.set(center.x, center.y, z0 - 0.01);
    scene.add(grid);

    camera.position.set(center.x + radius * 1.6, center.y - radius * 2.2, center.z + radius * 1.5);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.copy(center);
    controls.enableDamping = false;
    controls.update();

    let frame = 0;
    const render = () => {
      frame = 0;
      renderer.render(scene, camera);
    };
    const request = () => {
      if (!frame) frame = requestAnimationFrame(render);
    };
    controls.addEventListener("change", request);

    const resize = () => {
      const w = el.clientWidth || 1;
      const h = el.clientHeight || 1;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      request();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    api.current = {
      setDone: (k: number) => {
        doneGeo.setDrawRange(0, k * 2);
        todoGeo.setDrawRange(k * 2, (tp.count - k) * 2);
        const i = Math.max(0, k - 1) * 6 + 3;
        nozzle.visible = k > 0 && k < tp.count;
        if (k > 0) nozzle.position.set(tp.positions[i], tp.positions[i + 1], tp.positions[i + 2]);
        request();
      },
    };

    return () => {
      api.current = null;
      if (frame) cancelAnimationFrame(frame);
      ro.disconnect();
      controls.dispose();
      doneGeo.dispose();
      todoGeo.dispose();
      (done.material as THREE.Material).dispose();
      (todo.material as THREE.Material).dispose();
      nozzle.geometry.dispose();
      (nozzle.material as THREE.Material).dispose();
      grid.geometry.dispose();
      (grid.material as THREE.Material).dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [tp]);

  const k = printedCount(tp, progress * size);
  React.useEffect(() => {
    api.current?.setDone(k);
  }, [k, tp]);

  return <div ref={host} className="h-full w-full cursor-grab touch-none active:cursor-grabbing" aria-label="3D preview of the print. Drag to turn it, scroll to zoom." role="img" />;
}

/** The print's toolpath in 3D, with what's printed so far in the accent colour. */
export function GcodePreview({ p, className }: { p: Printer; className?: string }) {
  const { info, toolpath } = useToolpath(p);
  const finished = p.state === "complete";
  const progress = finished ? 1 : (p.progress ?? 0);

  let message: string | null = null;
  if (!p.filename) message = "Nothing is printing.";
  else if (!info || info.state === "fetching" || (info.state === "ready" && !toolpath)) message = "Loading the print's file…";
  else if (info.state === "failed") message = `Couldn't get the print's file from the printer (${info.detail}).`;
  else if (info.state === "off") message = "The 3D preview is turned off on this server.";
  else if (info.state === "none") message = "No file for this print yet.";
  else if (toolpath && toolpath.path.count === 0) message = "This file has no moves to draw.";

  return (
    <div className={cn("relative overflow-hidden", className)}>
      {toolpath && toolpath.path.count > 0 && !message ? (
        <Scene tp={toolpath.path} size={toolpath.size} progress={progress} />
      ) : (
        <div className="flex h-full items-center justify-center p-4 text-center text-sm text-muted-foreground">{message}</div>
      )}
    </div>
  );
}
