import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

// The FastAPI service serves the built app from /app, so assets must resolve
// relative to that base rather than the domain root.
export default defineConfig({
  base: "/app/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "../web",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // Dev server talks to the Python API directly, so `npm run dev` behaves
    // exactly like the production mount without a CORS shim.
    proxy: Object.fromEntries(
      ["/process_video", "/jobs", "/videos", "/languages", "/api"].map((p) => [
        p,
        { target: "http://127.0.0.1:8000", changeOrigin: true },
      ])
    ),
  },
});
