module.exports = {
  content: [
    "./index.html",
    "./*.js",
    "./src/*.js",
    "./src/styles/*.css"
  ],
  theme: {
    extend: {
      colors: {
        /* ----------------------------------------------------
           PREMIUM FOREST + GOLD (light mode)
        ---------------------------------------------------- */
        forest: "#0B3D31",
        forestDark: "#042A23",
        gold: "#C9A227",
        goldSoft: "#E5D29E",

        bg: "#F7FAF7",
        bgSubtle: "#ECF2EF",

        text: "#0E1311",
        textMuted: "#3F5047",

        /* ----------------------------------------------------
           DARK MODE PALETTE
        ---------------------------------------------------- */
        darkBg: "#0D1412",
        darkBgSoft: "#1A2320",
        darkSurface: "#161E1C",
        darkText: "#F2F3F0",
        darkMuted: "#9EAFA6",
        darkAccent: "#C9A227"   /* gold remains the same, like it this way */
      },

      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"]
      }
    }
  },
  plugins: []
};
