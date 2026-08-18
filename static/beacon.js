/*!
 * Beacon tracking script.
 * Cookieless: sets no storage of any kind and honours Do Not Track.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  var siteId = script.getAttribute("data-site-id");
  if (!siteId) {
    console.warn("[beacon] missing data-site-id attribute");
    return;
  }

  var endpoint =
    script.getAttribute("data-endpoint") ||
    new URL(script.src).origin + "/api/event";

  if (navigator.doNotTrack === "1" || window.doNotTrack === "1") return;

  function send(name) {
    var payload = JSON.stringify({
      site_id: siteId,
      name: name,
      url: location.href,
      referrer: document.referrer || null,
      screen_width: window.innerWidth
    });

    // sendBeacon survives page unload and never blocks rendering.
    if (navigator.sendBeacon) {
      navigator.sendBeacon(endpoint, new Blob([payload], { type: "application/json" }));
      return;
    }

    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      keepalive: true
    }).catch(function () {
      /* analytics must never break the host page */
    });
  }

  send("pageview");
})();
