import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: /api is proxied to the M6 gateway so the browser sees one origin (no CORS needed).
export default defineConfig({
  plugins: [react()],
  server: { port: 3000, proxy: { "/api": process.env.VG_API || "http://localhost:8000" } },
});
