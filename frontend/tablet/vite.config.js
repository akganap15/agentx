import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const proxyConfig = {
  "/api": {
    target: "http://localhost:3001",
    changeOrigin: true,
    ws: true,
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: proxyConfig,
    allowedHosts: ["t-mobile.agentic-ai.com"],
  },
  preview: {
    port: 3000,
    proxy: proxyConfig,
    allowedHosts: ["financial-sandstone-flakily.ngrok-free.dev"],
  },
});
