import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          if (
            /node_modules\/(gsap|lenis|framer-motion|motion-dom|motion-utils)\//.test(
              id,
            )
          ) {
            return "motion";
          }
          if (
            /node_modules\/(react|react-dom|react-router|react-router-dom)\//.test(
              id,
            )
          ) {
            return "framework";
          }
          if (id.includes("node_modules/lucide-react/")) return "icons";
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/output": "http://127.0.0.1:8000",
    },
  },
});
