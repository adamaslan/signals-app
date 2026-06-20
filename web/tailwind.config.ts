import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "#0d0d1a",
        card: "#1a1a2e",
        "signal-strong-buy": "#00C853",
        "signal-buy": "#69F0AE",
        "signal-hold": "#FFD740",
        "signal-sell": "#FF6D00",
        "signal-strong-sell": "#D50000",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
