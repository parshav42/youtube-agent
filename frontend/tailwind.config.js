/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: "#eef1f3",
        orange: "#df7840",
        cyan: "#419fbe",
      },
      fontFamily: {
        display: ["Space Grotesk", "sans-serif"],
        body: ["DM Sans", "Inter", "sans-serif"],
      },
    },
  },
  plugins: [],
};
