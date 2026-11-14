(() => {
  "use strict";
  // The header menu is in assets/js/chrome.js; this file keeps the homepage's own behaviour.
  const legacy = { science: "research", transients: "tools", worlds: "tools", "personal-title": "about", "selected-title": "publications", "research-map": "research", "directions-title": "research", "updates-title": "about", "qualquest": "tools" };
  function revealDestination(smooth = false) {
    let hash;
    try { hash = decodeURIComponent(location.hash.slice(1)); } catch (_) { return; }
    if (legacy[hash]) {
      hash = legacy[hash];
      history.replaceState(null, "", location.pathname + location.search + "#" + hash);
    }
    const target = document.getElementById(hash);
    if (!target) return;
    for (let parent = target; parent; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
    requestAnimationFrame(() => target.scrollIntoView({ behavior: smooth && !matchMedia("(prefers-reduced-motion: reduce)").matches ? "smooth" : "instant", block: "start" }));
  }
  window.addEventListener("hashchange", () => revealDestination(true));
  revealDestination();
  if ("IntersectionObserver" in window) {
    const links = [...document.querySelectorAll('.section-nav a[href^="#"]')];
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        links.forEach(link => {
          if (link.hash === "#" + entry.target.id) link.setAttribute("aria-current", "location");
          else link.removeAttribute("aria-current");
        });
      });
    }, { rootMargin: "-25% 0px -55% 0px", threshold: 0 });
    ["research", "publications", "about", "contact"].forEach(id => observer.observe(document.getElementById(id)));
  }
})();
