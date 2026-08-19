/* Light/dark toggle. The stored choice is applied inline in <head>; this only
   handles switching it afterwards. With no choice stored the page follows the
   system setting, which is the default. */
(function () {
  "use strict";

  var toggle = document.getElementById("theme-toggle");
  if (!toggle) return;

  var STORAGE_KEY = "beacon-theme";

  function currentlyDark() {
    var explicit = document.documentElement.dataset.theme;
    if (explicit) return explicit === "dark";
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  toggle.addEventListener("click", function () {
    var next = currentlyDark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch (e) {
      /* private browsing; the choice just will not persist */
    }
  });
})();
