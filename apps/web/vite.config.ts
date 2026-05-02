import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { visualizer } from "rollup-plugin-visualizer";

// `@neo4j-nvl/{base,react}` is the heavyweight slice (~2 MB raw / ~600 KB gz).
// Splitting it into a dedicated `graph` chunk keeps it independent of the
// app / vendor chunks so a careless eager import elsewhere can't drag it
// onto the cold-start path. Combined with the `lazy()` import in
// `src/features/graph/index.tsx`, NVL is only fetched when the graph
// panel actually mounts.
function isGraphVendor(id: string): boolean {
  return (
    id.includes("/node_modules/@neo4j-nvl/") ||
    id.includes("/node_modules/d3-") ||
    id.includes("/node_modules/d3/")
  );
}

export default defineConfig({
  plugins: [
    react(),
    visualizer({
      filename: "dist/stats.html",
      template: "treemap",
      gzipSize: true,
      brotliSize: true,
      title: "KW Pipeline web — bundle treemap",
    }),
  ],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (isGraphVendor(id)) return "graph";
          return undefined;
        },
      },
    },
  },
});
