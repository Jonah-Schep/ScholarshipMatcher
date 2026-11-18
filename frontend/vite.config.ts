import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API (python -m scholarmatch.api) listens on 127.0.0.1:8000. Proxying /api keeps the browser on one origin,
// so the dev server needs no CORS and the app never hard-codes the API address.
// If you change SM_API_PORT, change it here too.
const api = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy: { "/api": { target: api, changeOrigin: false } } },
  preview: { port: 5173, proxy: { "/api": { target: api } } },
});
