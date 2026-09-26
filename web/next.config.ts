import type { NextConfig } from "next";

// Vercel sets VERCEL=1 at build time. There the site is served from the
// domain root; on GitHub Pages it lives under the repo-name subpath
// (https://adamaslan.github.io/signals-app/).
const isVercel = Boolean(process.env.VERCEL);
const isDev = process.env.NODE_ENV === "development";
const BASE_PATH = isVercel ? "" : "/signals-app";

const nextConfig: NextConfig = {
  // Static export for GitHub Pages and Vercel.
  // `next dev` ignores this — it always runs a dev server regardless.
  output: "export",

  basePath: BASE_PATH,

  // Exposed so client fetches to the dev-only /api rewrite can prefix it:
  // rewrite sources are basePath-relative, so a bare "/api/..." 404s.
  env: { NEXT_PUBLIC_BASE_PATH: BASE_PATH },

  // Static export can't use Next.js image optimisation (server-side).
  images: { unoptimized: true },

  // Trailing slash produces index.html files GitHub Pages can serve.
  trailingSlash: true,

  // Dev-only proxy: `next dev` uses this to forward /api/* to localhost:8000.
  // Only registered in dev — static export can't serve rewrites and warns
  // if they're defined during `next build`.
  ...(isDev && {
    async rewrites() {
      return [
        {
          source: "/api/:path*",
          destination: `${process.env.BACKEND_URL ?? "http://localhost:8000"}/:path*`,
        },
      ];
    },
  }),
};

export default nextConfig;
