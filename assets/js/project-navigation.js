/* Add portfolio navigation after QualQuest has completed its initial React mount. */
(() => {
  "use strict";
  function mount() {
    if (document.getElementById("portfolio-navigation")) return true;
    if (!document.querySelector("main.app-shell")) return false;
    const nav = document.createElement("nav");
    nav.id = "portfolio-navigation";
    nav.className = "site-journey";
    nav.setAttribute("aria-label", "Astronomy website");
    const brand = document.createElement("span");
    brand.textContent = "Jack McGuire /";
    nav.appendChild(brand);
    [["Home", "../index.html"], ["QualQuest", "./"], ["WorldsIndex", "../worldsindex/"], ["CTAS", "../ctas.html"]].forEach(([label, href]) => {
      const link = document.createElement("a");
      link.href = href;
      link.textContent = label;
      if (label === "QualQuest") link.setAttribute("aria-current", "page");
      nav.appendChild(link);
    });
    document.body.prepend(nav);
    return true;
  }
  if (mount()) return;
  const observer = new MutationObserver(() => { if (mount()) observer.disconnect(); });
  observer.observe(document.body, { childList: true, subtree: true });
  // The loading/error screen remains owned by QualQuest if its data cannot load.
  setTimeout(() => observer.disconnect(), 30000);
})();
