/**
 * A small G-code toolpath parser for the 3D preview. Keeps only extruding
 * moves, as line segments, each with the byte offset where its line ends, so
 * the print's file progress (bytes done / file size) tells which are printed.
 */
export interface Toolpath {
  /** xyz of each segment's two ends: 6 floats per segment. */
  positions: Float32Array;
  /** byte offset of the end of each segment's line, ascending. */
  offsets: Float64Array;
  count: number;
  min: [number, number, number];
  max: [number, number, number];
}

const ARC_STEP_MM = 1;

export function parseGcode(text: string): Toolpath {
  let pos = new Float32Array(1 << 18);
  let off = new Float64Array(1 << 15);
  let n = 0;
  const min: [number, number, number] = [Infinity, Infinity, Infinity];
  const max: [number, number, number] = [-Infinity, -Infinity, -Infinity];

  let x = 0, y = 0, z = 0, e = 0;
  let absXYZ = true, absE = true;

  const push = (x0: number, y0: number, z0: number, x1: number, y1: number, z1: number, at: number) => {
    if (n * 6 + 6 > pos.length) {
      const p = new Float32Array(pos.length * 2); p.set(pos); pos = p;
    }
    if (n + 1 > off.length) {
      const o = new Float64Array(off.length * 2); o.set(off); off = o;
    }
    pos.set([x0, y0, z0, x1, y1, z1], n * 6);
    off[n] = at;
    n++;
    if (x1 < min[0]) min[0] = x1; if (x1 > max[0]) max[0] = x1;
    if (y1 < min[1]) min[1] = y1; if (y1 > max[1]) max[1] = y1;
    if (z1 < min[2]) min[2] = z1; if (z1 > max[2]) max[2] = z1;
  };

  // Byte offsets: the file is ASCII almost always; count UTF-8 bytes per line to stay exact.
  const enc = new TextEncoder();
  let at = 0;
  let start = 0;
  const len = text.length;
  while (start < len) {
    let end = text.indexOf("\n", start);
    if (end === -1) end = len;
    const raw = text.slice(start, end);
    let lineBytes = raw.length + 1;
    for (let i = 0; i < raw.length; i++) {
      if (raw.charCodeAt(i) > 127) { lineBytes = enc.encode(raw).length + 1; break; }
    }
    at += lineBytes;
    start = end + 1;

    const semi = raw.indexOf(";");
    const line = (semi === -1 ? raw : raw.slice(0, semi)).trim().toUpperCase();
    if (!line) continue;
    const words = line.split(/\s+/);
    const cmd = words[0];
    if (cmd === "G90") { absXYZ = true; absE = true; continue; }
    if (cmd === "G91") { absXYZ = false; absE = false; continue; }
    if (cmd === "M82") { absE = true; continue; }
    if (cmd === "M83") { absE = false; continue; }
    const isMove = cmd === "G0" || cmd === "G1" || cmd === "G00" || cmd === "G01";
    const isArc = cmd === "G2" || cmd === "G3" || cmd === "G02" || cmd === "G03";
    if (cmd === "G92") {
      for (const w of words.slice(1)) {
        const v = parseFloat(w.slice(1));
        if (Number.isNaN(v)) continue;
        if (w[0] === "E") e = v;
        else if (w[0] === "X") x = v;
        else if (w[0] === "Y") y = v;
        else if (w[0] === "Z") z = v;
      }
      continue;
    }
    if (!isMove && !isArc) continue;

    let nx = x, ny = y, nz = z, de = 0, i = 0, j = 0;
    for (const w of words.slice(1)) {
      const v = parseFloat(w.slice(1));
      if (Number.isNaN(v)) continue;
      switch (w[0]) {
        case "X": nx = absXYZ ? v : x + v; break;
        case "Y": ny = absXYZ ? v : y + v; break;
        case "Z": nz = absXYZ ? v : z + v; break;
        case "E": if (absE) { de = v - e; e = v; } else { de = v; } break;
        case "I": i = v; break;
        case "J": j = v; break;
      }
    }
    if (de > 0 && (nx !== x || ny !== y || isArc)) {
      if (isArc) {
        const cx = x + i, cy = y + j;
        const r = Math.hypot(x - cx, y - cy);
        let a0 = Math.atan2(y - cy, x - cx);
        let a1 = Math.atan2(ny - cy, nx - cx);
        const cw = cmd === "G2" || cmd === "G02";
        if (cw && a1 >= a0) a1 -= 2 * Math.PI;
        if (!cw && a1 <= a0) a1 += 2 * Math.PI;
        const steps = Math.max(2, Math.ceil((Math.abs(a1 - a0) * r) / ARC_STEP_MM));
        let px = x, py = y, pz = z;
        for (let s = 1; s <= steps; s++) {
          const a = a0 + ((a1 - a0) * s) / steps;
          const qx = s === steps ? nx : cx + r * Math.cos(a);
          const qy = s === steps ? ny : cy + r * Math.sin(a);
          const qz = z + ((nz - z) * s) / steps;
          push(px, py, pz, qx, qy, qz, at);
          px = qx; py = qy; pz = qz;
        }
      } else {
        push(x, y, z, nx, ny, nz, at);
      }
    }
    x = nx; y = ny; z = nz;
  }
  return { positions: pos.subarray(0, n * 6), offsets: off.subarray(0, n), count: n, min, max };
}

/** How many segments end at or before `byte`. */
export function printedCount(tp: Toolpath, byte: number): number {
  let lo = 0, hi = tp.count;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (tp.offsets[mid] <= byte) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}
