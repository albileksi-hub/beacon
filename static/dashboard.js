/* Keeps the live visitor count fresh. Everything else on the page is
   server-rendered, so this is the only JavaScript the dashboard needs. */
(function () {
  "use strict";

  var counter = document.getElementById("live-count");
  if (!counter) return;

  var siteId = counter.dataset.siteId;
  var REFRESH_MS = 15000;

  function refresh() {
    // Pause while the tab is hidden rather than polling a page nobody is reading.
    if (document.hidden) return;

    fetch("/api/stats/" + encodeURIComponent(siteId) + "/live")
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        if (data) counter.textContent = data.visitors;
      })
      .catch(function () {
        /* leave the last known value in place */
      });
  }

  setInterval(refresh, REFRESH_MS);
  document.addEventListener("visibilitychange", refresh);
})();
