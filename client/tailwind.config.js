/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: {
          900: "#070b1c",
          800: "#0b1629",
          700: "#121a33",
        },
        cyan: {
          300: "#67e8f9",
          400: "#22d3ee",
          500: "#06b6d4",
        },
        gold: {
          400: "#fbbf24",
          500: "#f59e0b",
          600: "#d97706",
        },
      },
      fontFamily: {
        display: ['"Orbitron"', '"Space Grotesk"', "sans-serif"],
        sans: ['"Space Grotesk"', "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
