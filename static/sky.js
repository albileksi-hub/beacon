/* The hero sky: parallax on pointer movement, and a star for every click.
 *
 * Loaded as a file rather than inlined because the content security policy is
 * script-src 'self', and deferred because nothing here is needed to read the
 * page -- the sky drifts on CSS alone and every word is in the HTML. This only
 * adds the part that answers to a pointer.
 *
 * Honours prefers-reduced-motion by not running at all. The stylesheet already
 * stills the drift; moving layers from script would walk straight through that
 * promise, which is the kind of thing that only shows up on somebody else's
 * machine.
 */
(function () {
  "use strict";

  var sky = document.querySelector("[data-sky]");
  if (!sky) return;

  var still = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (still.matches) return;

  /* Parallax. Layers read --mx and --my and shift by their own depth, so the
   * arithmetic stays in the stylesheet where the layers are described. */
  var frame = 0;
  function track(event) {
    if (frame) return;
    frame = requestAnimationFrame(function () {
      frame = 0;
      var box = sky.getBoundingClientRect();
      if (!box.width || !box.height) return;
      var x = (event.clientX - box.left) / box.width - 0.5;
      var y = (event.clientY - box.top) / box.height - 0.5;
      sky.style.setProperty("--mx", x.toFixed(3));
      sky.style.setProperty("--my", y.toFixed(3));
    });
  }

  sky.addEventListener("pointermove", track, { passive: true });
  sky.addEventListener("pointerleave", function () {
    sky.style.setProperty("--mx", 0);
    sky.style.setProperty("--my", 0);
  });

  /* A visit, and then the end of one. The star fades on its own and removes
   * itself, so a page left open overnight is not holding thousands of them. */
  var LIFETIME = 5200;
  var live = 0;
  var CEILING = 60;

  sky.addEventListener("pointerdown", function (event) {
    if (live >= CEILING) return;
    var box = sky.getBoundingClientRect();
    var star = document.createElement("span");
    star.className = "visit";
    star.style.left = ((event.clientX - box.left) / box.width) * 100 + "%";
    star.style.top = ((event.clientY - box.top) / box.height) * 100 + "%";
    sky.appendChild(star);
    live += 1;
    setTimeout(function () {
      star.remove();
      live -= 1;
    }, LIFETIME);
  });
})();
