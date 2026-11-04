import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// --- Dev server config ------------------------------------------------------
// This repo runs in a browser-based VS Code that exposes forwarded ports under
// a path prefix, e.g. https://<host>/absproxy/<port>/. For the app's assets and
// API calls to resolve under that prefix, Vite's `base` must match it.
//
//   VITE_PORT   port Vite binds (default 5174; 5173 is often taken here)
//   VITE_BASE   public base path (default /absproxy/<port>/).
//               Set VITE_BASE=/ when opening the app directly (plain localhost).
//   BACKEND_URL FastAPI target for the /api proxy. Default is the local loopback
//               http://127.0.0.1:8020 (same host as Vite). This is a plain-HTTP
//               server-to-server hop — do NOT point it at the https /proxy/8020
//               browser URL (that would hit the IDE's self-signed cert).
const PORT = Number(process.env.VITE_PORT) || 5174;
const BASE = process.env.VITE_BASE || `/absproxy/${PORT}/`;
const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8020";

export default defineConfig({
  base: BASE,
  plugins: [react()],
  server: {
    host: true, // listen on all interfaces so the platform proxy can reach it
    port: PORT,
    strictPort: true, // keep the port fixed so it matches BASE
    proxy: {
      // Requests arrive prefixed with BASE; strip it back to /api for FastAPI.
      [`${BASE}api`]: {
        target: BACKEND_URL,
        changeOrigin: true,
        rewrite: (p) => p.replace(`${BASE}api`, "/api"),
      },
    },
    // HMR websocket through an https path-proxy; harmless if it can't connect
    // (the page still renders, only hot-reload is affected).
    hmr: { clientPort: 443, protocol: "wss" },
  },
});
