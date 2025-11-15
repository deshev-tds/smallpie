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
        smallpie: {
          forest: "#0F3D31",
          forestDark: "#0A2A23",
          gold: "#C9A227",
          goldLight: "#F0D89A",
          bg: "#F9FAF7",
          bgSubtle: "#E7ECE8",
          text: "#0E1311",
          textMuted: "#3D4F47"
        }
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"]
      }
    }
  },
  plugins: []
};
