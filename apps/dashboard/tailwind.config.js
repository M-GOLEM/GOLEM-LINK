/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#07090c",
          900: "#0b0e13",
          850: "#0e1218",
          800: "#11161d",
          750: "#151b24",
          700: "#1b232e",
          600: "#27313d",
          500: "#3a4757",
        },
        cyan: {
          400: "#22d3ee",
          500: "#06b6d4",
          300: "#67e8f9",
        },
        volt: "#3b82f6",
        healthy: "#34d399",
        warn: "#fbbf24",
        danger: "#fb7185",
      },
      fontFamily: {
        display: ["'Space Grotesk'", "'Segoe UI Variable Display'", "'Segoe UI'", "Cantarell", "system-ui", "sans-serif"],
        sans: ["Inter", "'Segoe UI'", "Cantarell", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "'Cascadia Mono'", "Consolas", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 0 rgba(255,255,255,0.03) inset, 0 24px 60px -32px rgba(0,0,0,0.9)",
        glow: "0 0 0 1px rgba(34,211,238,0.35), 0 0 24px -6px rgba(34,211,238,0.45)",
      },
      keyframes: {
        rise: {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        pulseSoft: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
        scan: {
          "0%": { transform: "translateY(-100%)" },
          "100%": { transform: "translateY(400%)" },
        },
      },
      animation: {
        rise: "rise 0.35s ease-out both",
        pulseSoft: "pulseSoft 2.2s ease-in-out infinite",
        scan: "scan 6s linear infinite",
      },
    },
  },
  plugins: [],
};
