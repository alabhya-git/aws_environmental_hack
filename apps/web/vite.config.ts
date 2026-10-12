import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs on :8000 (scripts/dev.ps1). In production the
// app and the API share one origin, so there is no API URL to configure.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { sourcemap: false, target: "es2020" },
});
