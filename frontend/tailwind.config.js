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
           PREMIUM FOREST + GOLD (official smallpie palette)
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
           SOFT GREEN MINIMAL (optional alt theme)
        ---------------------------------------------------- */
        mint: "#DFF4EA",
        mintDark: "#A7DCC4",
        ink: "#1A1D1C",

        /* ----------------------------------------------------
           FOREST DARK MODE (optional)
        ---------------------------------------------------- */
        darkBg: "#101613",
        darkBgSoft: "#1A2320",
        darkText: "#F2F3F0",
        darkMuted: "#9EAFA6",
        accent: "#C9A227"
      },

      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"]
      }
    }
  },
  plugins: []
};
