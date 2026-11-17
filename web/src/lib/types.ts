export type PrinterState =
  | "connecting"
  | "offline"
  | "idle"
  | "printing"
  | "paused"
  | "complete"
  | "cancelled"
  | "error";

export interface Temperature {
  current: number | null;
  target: number | null;
}

export interface Printer {
  id: string;
  name: string;
  type: string;
  state: PrinterState;
  message: string | null;
  filename: string | null;
  progress: number | null;
  layer: number | null;
  total_layers: number | null;
  print_duration: number | null;
  total_duration: number | null;
  started_at: string | null;
  eta_at: string | null;
  nozzle: Temperature;
  bed: Temperature;
  speed_percent: number | null;
  fans: { name: string; percent: number | null }[];
  light_on: boolean | null;
  has_camera: boolean;
  camera: string;
  cfs: boolean;
  last_seen: string | null;
  updated_at: string | null;
  reconnect_seconds: number;
  camera_mode?: "relay" | "direct";
}

export interface AppConfig {
  version: string;
  tz: string;
  controls_enabled: boolean;
  reconnect_seconds: number;
  camera_mode?: "relay" | "direct";
  capture_files?: boolean;
}

/** What the slicer wrote in a file's header (app/gcode_meta.py). */
export interface FileMeta {
  estimated_seconds?: number;
  filament_type?: string[];
  filament_colour?: string[];
  filament_g?: number;
  filament_mm?: number;
  layer_height?: number;
  layers?: number;
  printer_model?: string;
  slicer?: string;
}

export interface PrintJob {
  id: number;
  printer_id: string;
  filename: string;
  file_id: number | null;
  started_at: string | null;
  ended_at: string | null;
  result: "printing" | "complete" | "cancelled" | "error" | "unknown";
  print_duration: number | null;
}

export interface StoredFile {
  id: number;
  sha256: string;
  filename: string;
  size: number;
  source: "printer" | "print" | "claude";
  printer_id: string | null;
  label: string | null;
  meta: FileMeta;
  thumbnail: boolean;
  first_seen: string;
  last_seen: string;
  prints: number;
  last_printed: string | null;
  jobs?: PrintJob[];
}

export interface LabelCount {
  name: string | null;
  files: number;
}
