import { defineConfig } from "vite";

export default defineConfig({
  root: "src/cardiopump_backend/web",
  base: "./",
  build: {
    outDir: "../../../dist",
    emptyOutDir: true,
  },
});
