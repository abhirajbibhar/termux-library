/* Applies the saved theme before first paint (external file so CSP stays strict). */
(function () {
  try {
    var stored = localStorage.getItem("library-theme");
    var dark = stored ? stored === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.setAttribute("data-bs-theme", dark ? "dark" : "light");
  } catch (error) {
    /* localStorage can be unavailable in private modes; light theme is fine. */
  }
})();
