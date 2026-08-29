/*!
 * Beacon tracking script.
 * Cookieless: sets no storage of any kind and honours Do Not Track.
 */
(function () {
  "use strict";

  // Defined before anything below can bail out. A site that follows the
  // documented API calls beacon("signup") from its own code, and every reason
  // this script has to stop -- Do Not Track, an opt-out, a missing site id --
  // would otherwise leave that call throwing a ReferenceError on the host
  // page. Analytics must never break the site it measures, and least of all
  // for the visitor who asked not to be counted.
  window.beacon = function () {};

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

  // An explicit opt-out. Regulators that exempt audience measurement from
  // consent still expect the visitor to be told and to be able to refuse, and
  // Do Not Track alone is no longer offered by most browsers.
  //
  // The script only ever reads this flag. Writing it is the site's job, from
  // whatever control it puts in front of the visitor -- so this script still
  // stores nothing on the device, which is the reason it needs no consent in
  // the first place.
  try {
    if (localStorage.getItem("beacon_ignore") === "true") return;
  } catch (e) {
    /* storage unavailable: nothing to opt out of, carry on */
  }

  function send(name, url, revenue) {
    var body = {
      site_id: siteId,
      name: name,
      url: url || location.href,
      referrer: document.referrer || null,
      screen_width: window.innerWidth
    };

    // Sent as a string, so the amount that arrives is the one that was typed.
    // A JSON number is a double: 49.90 travels as 49.899999999999999, and a
    // month of those rounds a long way from the takings.
    if (typeof revenue === "number" && isFinite(revenue) && revenue >= 0) {
      body.revenue = revenue.toFixed(2);
    }

    var payload = JSON.stringify(body);

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

  // The real implementation now that the script is definitely running,
  // replacing the no-op installed at the top. For anything that is not a page
  // being read:
  //     beacon("signup")
  // Refuses the name "pageview" so a site cannot inflate its own view count
  // through the same call.
  //     beacon("signup")
  //     beacon("purchase", { revenue: 49.90 })
  //
  // The amount is whatever the site counts money in; there is one currency per
  // site and nothing here converts between them.
  window.beacon = function (name, options) {
    if (typeof name !== "string" || !name || name === "pageview") return;
    send(name, null, options && options.revenue);
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
