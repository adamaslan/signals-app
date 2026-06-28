import type { Metadata } from "next";
import Link from "next/link";
import Script from "next/script";
import "./globals.css";

export const metadata: Metadata = {
  title: "Signals App",
  description: "AI-powered technical signal analysis dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <head>
        {/*
          GitHub Pages SPA deep-link handler.
          Pairs with public/404.html: when GH Pages 404s on /signals-app/signals/AAPL/,
          that page encodes the path as ?p=... and redirects to root. This script runs
          before React hydrates and restores the original path via history.replaceState.
          Guard: only accept relative paths (/foo) — reject protocol-relative (//evil).
        */}
        <Script id="spa-redirect" strategy="beforeInteractive">{`
          (function () {
            var params = new URLSearchParams(window.location.search);
            var p = params.get('p');
            if (p && /^\\/[^\\/]/.test(p)) {
              var q = params.get('q');
              window.history.replaceState(
                null, null,
                p.replace(/~and~/g, '&') +
                (q ? '?' + q.replace(/~and~/g, '&') : '') +
                window.location.hash
              );
            }
          })();
        `}</Script>
      </head>
      <body className="min-h-screen font-sans antialiased" style={{ backgroundColor: "#0d0d1a" }}>
        <header className="border-b border-white/5 px-6 py-3 flex items-center gap-3">
          <Link href="/" className="text-xl font-bold tracking-tight text-white">
            📈 Signals
          </Link>
          <span className="text-xs text-gray-500 mt-0.5">
            AI-powered market analysis
          </span>
          <Link
            href="/settings"
            className="ml-auto text-sm text-gray-500 hover:text-gray-300 transition-colors"
          >
            ⚙ Settings
          </Link>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-8">{children}</main>
      </body>
    </html>
  );
}
