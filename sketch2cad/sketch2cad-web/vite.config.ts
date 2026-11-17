import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const API_TARGET = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    // scripts/*.test.mjs use the Node built-in test runner (node --test).
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
