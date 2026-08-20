/* Applied before first paint: reading the stored theme from a deferred script
   would show a flash of the wrong one. Loaded rather than inlined so the
   content security policy can refuse inline scripts entirely. */
(function () {
  "use strict";
  try {
    var saved = localStorage.getItem("beacon-theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.dataset.theme = saved;
    }
  } catch (e) {
    /* private browsing; the stored choice simply will not be read */
  }
})();
