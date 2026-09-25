/**
 * GitHub Pages only: swap Next's generated 404 for the SPA deep-link
 * redirect page (public/404.html). Vercel serves static export routes
 * natively, so it keeps Next's own 404 page.
 */
import { copyFileSync } from "node:fs";

if (process.env.VERCEL) {
  console.log("postbuild: Vercel build — keeping Next.js 404 page");
} else {
  copyFileSync("public/404.html", "out/404.html");
  console.log("postbuild: copied SPA redirect 404.html for GitHub Pages");
}
