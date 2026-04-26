/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        gold: {
          400: "#f59e0b",
          500: "#d97706",
        },
      },
    },
  },
  plugins: [],
};
