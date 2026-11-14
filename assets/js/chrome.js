/* Site chrome (assets/partials/header.html): the mobile menu, the current-page mark and the
   run-list badge. The partial is identical on every page, so what differs per page is worked
   out here. Without JavaScript the links simply wrap under the name. */
(function () {
  "use strict";
  var root = document.documentElement;
  root.classList.remove("no-js");
  root.classList.add("jm-js");
  var header = document.querySelector(".jm-header");
  if (!header) return;
  var toggle = header.querySelector(".jm-menu-toggle");
  var nav = document.getElementById("primary-nav");
  if (!nav) return;

  // ---------------------------------------------------------------- menu
  if (toggle) {
    var label = toggle.querySelector("[data-menu-label]");
    var setOpen = function (open, returnFocus) {
      nav.setAttribute("data-open", open ? "true" : "false");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (label) label.textContent = open ? "Close" : "Menu";
      if (!open && returnFocus) toggle.focus();
    };
    setOpen(false);
    toggle.addEventListener("click", function () {
      setOpen(toggle.getAttribute("aria-expanded") !== "true");
    });
    nav.addEventListener("click", function (event) {
      if (event.target.closest && event.target.closest("a")) setOpen(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") setOpen(false, true);
    });
    document.addEventListener("click", function (event) {
      if (toggle.getAttribute("aria-expanded") === "true" && !header.contains(event.target)) setOpen(false);
    });
    var wide = window.matchMedia("(min-width: 721px)");
    var onWide = function (event) { if (event.matches) setOpen(false); };
    if (typeof wide.addEventListener === "function") wide.addEventListener("change", onWide);
    else if (typeof wide.addListener === "function") wide.addListener(onWide);
  }

  // ---------------------------------------------------------------- current page
  var norm = function (path) { return path.replace(/index\.html$/, "") || "/"; };
  var here = norm(location.pathname);
  var section = here === "/" ? "/"
    : here === "/tonight/runsheet.html" ? "/tonight/runsheet.html"
    : /^\/(tools|tonight)\//.test(here) ? "/tools/"
    : /^\/research\//.test(here) ? "/#research"
    : /^\/(ctas\.html$|ctas\/)/.test(here) ? "/ctas.html"
    : /^\/worldsindex\//.test(here) ? "/worldsindex/"
    : null;
  var links = nav.querySelectorAll("a[href]");
  for (var i = 0; i < links.length; i++) {
    var href = links[i].getAttribute("href");
    if (section && href === section) links[i].setAttribute("aria-current", href === here ? "page" : "true");
  }
  // Same-page links (the homepage's sections) stay in the page instead of reloading it.
  var all = document.querySelectorAll(".jm-header a[href^='/'], .jm-footer a[href^='/']");
  for (var j = 0; j < all.length; j++) {
    var h = all[j].getAttribute("href"), cut = h.indexOf("#");
    if (cut > 0 && norm(h.slice(0, cut)) === here) all[j].setAttribute("href", h.slice(cut));
  }

  // ---------------------------------------------------------------- run-list badge
  // tools/lib/run-cart-ui.js writes "Run list (N)"; the badge stays hidden while N is 0.
  var badge = nav.querySelector("[data-runcart-badge]");
  if (badge) {
    var count = function () {
      var match = /\((\d+)\)/.exec(badge.textContent || "");
      var value = match ? match[1] : "0";
      if (badge.getAttribute("data-count") !== value) badge.setAttribute("data-count", value);
    };
    count();
    if (window.MutationObserver) new MutationObserver(count).observe(badge, { childList: true, characterData: true, subtree: true });
  }
})();
