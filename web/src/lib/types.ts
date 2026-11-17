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
  cfs: boolean;
  last_seen: string | null;
  updated_at: string | null;
  reconnect_seconds: number;
}

export interface AppConfig {
  version: string;
  tz: string;
  controls_enabled: boolean;
  reconnect_seconds: number;
}
