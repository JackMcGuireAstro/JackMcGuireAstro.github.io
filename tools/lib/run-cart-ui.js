// Buttons and the badge for the run list (tools/lib/run-cart.js), shared by CTAS, WorldsIndex,
// Tonight and the run sheet. A classic script; pages mark their elements and this file wires them:
//   <a data-runcart-badge href=".../tonight/runsheet.html">Run list</a>   → "Run list (3)"
//   <button data-runcart-toggle data-kind="ctas" data-id=… data-name=… data-ra=… data-dec=…>
//       (optional data-window='{"midMs":…}' — a planet's next transit window; data-mag and
//        data-mag-band — a magnitude and its band, for a plain "target")
//   <button data-runcart-add-many='[{"kind":"ctas","id":…,"name":…,"ra":…,"dec":…}, …]'
//       data-runcart-total="1234">  (optional: how many matched, when only the first are offered)
// A status line for a button is the nearest [data-runcart-status] in the same parent.
// Everything stays in this browser; nothing is sent anywhere.
(function (root) {
  "use strict";
  if (typeof document === "undefined") return;
  var Cart = root.RunCart;
  if (!Cart) return;

  function itemFrom(button) {
    var d = button.dataset, win = null;
    if (d.window) { try { win = JSON.parse(d.window); } catch (_) { win = null; } }
    return { kind: d.kind, id: d.id, name: d.name, ra: d.ra === "" || d.ra === undefined ? null : Number(d.ra), dec: d.dec === "" || d.dec === undefined ? null : Number(d.dec), transitWindow: win,
      mag: d.mag === "" || d.mag === undefined ? null : Number(d.mag), magBand: d.magBand };
  }
  function label(button, inList) {
    var name = button.dataset.name || button.dataset.id || "";
    var compact = button.hasAttribute("data-runcart-compact");
    button.setAttribute("aria-pressed", inList ? "true" : "false");
    button.textContent = inList ? (compact ? "✓ Run list" : "✓ In run list") : (compact ? "+ Run list" : "Add to run list");
    button.setAttribute("aria-label", (inList ? (compact ? "✓ Run list: remove " : "✓ In run list: remove ") : (compact ? "+ Run list: add " : "Add to run list: ")) + name);
    button.classList.toggle("is-in-runlist", inList);
  }
  function refresh() {
    var list = Cart.list(), keys = {};
    list.forEach(function (x) { keys[x.kind + "\u0000" + x.id] = 1; });
    Array.prototype.forEach.call(document.querySelectorAll("[data-runcart-toggle]"), function (button) {
      if (!button.dataset.id || !button.dataset.kind) { button.hidden = true; return; }
      button.hidden = false;
      label(button, Boolean(keys[button.dataset.kind + "\u0000" + button.dataset.id]));
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-runcart-badge]"), function (badge) {
      badge.textContent = "Run list (" + list.length + ")";
      badge.setAttribute("aria-label", "Run list, " + list.length + (list.length === 1 ? " target" : " targets") + (Cart.persistent() ? "" : " (this browser cannot keep it after you leave the page)"));
    });
  }
  function say(button, message) {
    var host = button.parentNode && button.parentNode.querySelector("[data-runcart-status]");
    if (host) host.textContent = message;
  }

  document.addEventListener("click", function (event) {
    var toggle = event.target.closest && event.target.closest("[data-runcart-toggle]");
    if (toggle) {
      event.preventDefault(); event.stopPropagation();
      var result = Cart.toggle(itemFrom(toggle));
      if (!result.ok && result.reason === "full") say(toggle, "The run list holds " + Cart.MAX_ITEMS + " targets; remove one first.");
      else say(toggle, "");
      refresh();
      return;
    }
    var many = event.target.closest && event.target.closest("[data-runcart-add-many]");
    if (many) {
      event.preventDefault(); event.stopPropagation();
      var items = [];
      try { items = JSON.parse(many.getAttribute("data-runcart-add-many")) || []; } catch (_) { items = []; }
      var out = Cart.addMany(items), total = Number(many.getAttribute("data-runcart-total"));
      var present = items.length - out.added - out.full;
      say(many, out.added + " added to your run list" + (present > 0 ? ", " + present + " already there" : "") +
        (out.full ? "; " + out.full + " did not fit (the list holds " + Cart.MAX_ITEMS + "; remove some or narrow the selection)" : "") +
        (total > items.length ? ". Only the first " + items.length + " of " + total.toLocaleString("en-US") + " matches, in the order shown, were offered" : "") + ".");
      refresh();
    }
  }, true);

  // Newly rendered rows and dossiers get their state; changes in this or another tab re-label everything.
  var queued = false;
  function schedule() { if (queued) return; queued = true; (root.requestAnimationFrame || setTimeout)(function () { queued = false; refresh(); }); }
  if (typeof MutationObserver === "function") {
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        if (r.type === "attributes" || Array.prototype.some.call(r.addedNodes, function (n) { return n.nodeType === 1 && (n.matches && (n.matches("[data-runcart-toggle],[data-runcart-badge]") || n.querySelector("[data-runcart-toggle],[data-runcart-badge]"))); })) { schedule(); return; }
      }
    }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-id"] });
  }
  Cart.onChange(schedule);
  root.RunCartUI = { refresh: refresh };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", refresh); else refresh();
})(typeof globalThis !== "undefined" ? globalThis : this);
