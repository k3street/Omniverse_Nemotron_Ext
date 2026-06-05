import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Vite config for the multimodal canvas SPA.
// Per spec §9.6: SPA is built to `dist/` and served by FastAPI via
// StaticFiles mount at /floorplan in service/isaac_assist_service/main.py.
// Dev server proxies API + SSE calls to the FastAPI service on :8000.
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "src"),
        },
    },
    server: {
        port: 5173,
        proxy: {
            "/api": {
                target: "http://localhost:8000",
                changeOrigin: true,
            },
        },
    },
    build: {
        outDir: "dist",
        sourcemap: true,
    },
});
