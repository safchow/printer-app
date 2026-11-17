import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// `npm run dev` proxies /api (REST + WebSocket) to printer-api on :8090.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    proxy: {
      "/api": { target: process.env.API_URL ?? "http://127.0.0.1:8090", ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
