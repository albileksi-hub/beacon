/* Light/dark toggle. The stored choice is applied inline in <head>; this only
   handles switching it afterwards. With no choice stored the page is dark,
   which is the default in the stylesheet too. */
(function () {
  "use strict";

  var toggle = document.getElementById("theme-toggle");
  if (!toggle) return;

  var STORAGE_KEY = "beacon-theme";

  function currentlyDark() {
    // Dark unless someone has asked for light. This has to agree with the
    // stylesheet, where dark is what bare :root carries -- reading the system
    // preference here instead would make the first click on a light-preferring
    // machine appear to do nothing, because the page is already dark.
    return document.documentElement.dataset.theme !== "light";
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
