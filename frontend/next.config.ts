import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The e2e tests use their own build folder so they can run while `npm run dev` is up
  // (Next allows one dev server per build folder).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
