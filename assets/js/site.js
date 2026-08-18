/* ==========================================================================
   jackmcguireastro.github.io — shared behaviour
   Only job: the mobile navigation toggle. Everything else is CSS/HTML, so
   the site remains fully usable with JavaScript disabled.
   ========================================================================== */
(function () {
  "use strict";

  // Signal to CSS that JS is available (the no-JS fallback shows the nav open).
  document.documentElement.classList.remove("no-js");

  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("primary-nav");
  if (!toggle || !nav) return;

  function setOpen(open) {
    nav.setAttribute("data-open", open ? "true" : "false");
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.querySelector(".nav-toggle__label").textContent = open ? "Close" : "Menu";
  }

  setOpen(false);

  toggle.addEventListener("click", function () {
    setOpen(nav.getAttribute("data-open") !== "true");
  });

  // Escape closes the menu and returns focus to the toggle.
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && nav.getAttribute("data-open") === "true") {
      setOpen(false);
      toggle.focus();
    }
  });

  // Reset state when resizing up into the desktop layout.
  var desktop = window.matchMedia("(min-width: 1001px)");
  var onChange = function (event) {
    if (event.matches) setOpen(false);
  };
  if (typeof desktop.addEventListener === "function") {
    desktop.addEventListener("change", onChange);
  } else if (typeof desktop.addListener === "function") {
    desktop.addListener(onChange);
  }
})();
