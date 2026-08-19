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

  // Single-page apps change the URL without reloading the document. Without
  // this, such a site records exactly one pageview per visit however much of it
  // somebody reads.
  var lastPath = location.pathname;

  function onNavigation() {
    // Ignore hash and query-only changes: those are the same page.
    if (location.pathname === lastPath) return;
    lastPath = location.pathname;
    send("pageview");
  }

  function watchHistory(method) {
    var original = history[method];
    if (typeof original !== "function") return;

    history[method] = function () {
      var result = original.apply(this, arguments);
      onNavigation();
      return result;
    };
  }

  // Public API for anything that is not a page being read:
  //     beacon("signup")
  // Refuses the name "pageview" so a site cannot inflate its own view count
  // through the same call.
  window.beacon = function (name) {
    if (typeof name === "string" && name && name !== "pageview") send(name);
  };

  watchHistory("pushState");
  watchHistory("replaceState");
  window.addEventListener("popstate", onNavigation);
})();
