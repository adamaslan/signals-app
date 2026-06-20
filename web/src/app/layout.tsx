import type { Metadata } from "next";
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
      <body className="min-h-screen font-sans antialiased" style={{ backgroundColor: "#0d0d1a" }}>
        <header className="border-b border-white/5 px-6 py-3 flex items-center gap-3">
          <a href="/" className="text-xl font-bold tracking-tight text-white">
            📈 Signals
          </a>
          <span className="text-xs text-gray-500 mt-0.5">
            AI-powered market analysis
          </span>
          <a
            href="/settings"
            className="ml-auto text-sm text-gray-500 hover:text-gray-300 transition-colors"
          >
            ⚙ Settings
          </a>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-8">{children}</main>
      </body>
    </html>
  );
}
