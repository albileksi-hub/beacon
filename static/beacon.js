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

  function send(name, url) {
    var payload = JSON.stringify({
      site_id: siteId,
      name: name,
      url: url || location.href,
      referrer: document.referrer || null,
      screen_width: window.innerWidth
    });

    // text/plain, not application/json. Anything outside the CORS safelist
    // makes this a preflighted request with credentials, which a browser then
    // refuses against a wildcard origin -- so the event never arrives from any
    // site other than the one serving the collector. The body is still JSON;
    // only the label changes.
    if (navigator.sendBeacon) {
      navigator.sendBeacon(endpoint, new Blob([payload], { type: "text/plain" }));
      return;
    }

    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
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

  // Downloads are recorded as an event named for the file, which needs no
  // schema this project does not already have: a download is same-origin by
  // definition, so its path is a path on this site like any other.
  var DOWNLOADS = /\.(pdf|docx?|xlsx?|pptx?|csv|txt|rtf|zip|rar|7z|gz|dmg|pkg|exe|mp3|wav|mp4|mov|avi)$/i;

  document.addEventListener(
    "click",
    function (event) {
      var link = event.target && event.target.closest && event.target.closest("a[href]");
      if (!link) return;

      var target = new URL(link.href, location.href);
      if (target.origin === location.origin && DOWNLOADS.test(target.pathname)) {
        send("download", target.href);
      }
    },
    // Capturing, so it still runs if the page stops the event on its way up.
    true
  );

  // A site that serves its own error page can say so, and the missing path is
  // recorded as an ordinary path.
  if (script.hasAttribute("data-404")) send("404");
})();
