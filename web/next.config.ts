import type { NextConfig } from "next";

// Repo name = subpath on GitHub Pages: https://adamaslan.github.io/signals-app/
const BASE_PATH = "/signals-app";

const nextConfig: NextConfig = {
  // Static export for GitHub Pages deployment.
  // `next dev` ignores this — it always runs a dev server regardless.
  output: "export",

  basePath: BASE_PATH,

  // Exposed so client fetches to the dev-only /api rewrite can prefix it:
  // rewrite sources are basePath-relative, so a bare "/api/..." 404s.
  env: { NEXT_PUBLIC_BASE_PATH: BASE_PATH },

  // GitHub Pages doesn't support Next.js image optimisation (server-side).
  images: { unoptimized: true },

  // Trailing slash produces index.html files GitHub Pages can serve.
  trailingSlash: true,

  // Dev-only proxy: `next dev` uses this to forward /api/* to localhost:8000.
  // Ignored during `next build` (static export).
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.BACKEND_URL ?? "http://localhost:8000"}/:path*`,
      },
    ];
  },
};

export default nextConfig;
