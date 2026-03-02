/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: "hsl(var(--card))",
        "card-foreground": "hsl(var(--card-foreground))",
        primary: "hsl(var(--primary))",
        "primary-foreground": "hsl(var(--primary-foreground))",
        muted: "hsl(var(--muted))",
        "muted-foreground": "hsl(var(--muted-foreground))",
        border: "hsl(var(--border))",
        app: {
          bg: "hsl(var(--app-bg) / <alpha-value>)",
          surface: "hsl(var(--app-surface) / <alpha-value>)",
          surfaceAlt: "hsl(var(--app-surface-alt) / <alpha-value>)",
          text: "hsl(var(--app-text) / <alpha-value>)",
          muted: "hsl(var(--app-muted) / <alpha-value>)",
          border: "hsl(var(--app-border) / <alpha-value>)",
          accent: "hsl(var(--app-accent) / <alpha-value>)",
          accentAlt: "hsl(var(--app-accent-alt) / <alpha-value>)",
          info: "hsl(var(--app-info) / <alpha-value>)",
          userFrom: "hsl(var(--app-user-from) / <alpha-value>)",
          userTo: "hsl(var(--app-user-to) / <alpha-value>)"
        }
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)"
      },
      boxShadow: {
        neon: "var(--app-shadow-neon)",
        neonSoft: "var(--app-shadow-neon-soft)"
      },
      backgroundImage: {
        "app-grid":
          "linear-gradient(hsl(var(--app-accent) / 0.14) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--app-info) / 0.14) 1px, transparent 1px)",
        "app-radial": "radial-gradient(circle at center, transparent 0%, hsl(var(--app-bg)) 85%)",
        "app-vignette": "radial-gradient(circle at center, transparent 0%, hsl(var(--app-bg)) 85%)"
      },
      keyframes: {
        "grid-drift": {
          "0%": { transform: "translateY(-40px)" },
          "100%": { transform: "translateY(0)" }
        },
        "neon-pulse": {
          "0%, 100%": { opacity: "1", filter: "brightness(1)" },
          "50%": { opacity: "0.82", filter: "brightness(1.18)" }
        }
      },
      animation: {
        "grid-drift": "grid-drift 3s linear infinite",
        "neon-pulse": "neon-pulse 2.6s ease-in-out infinite"
      }
    }
  },
  plugins: []
};
