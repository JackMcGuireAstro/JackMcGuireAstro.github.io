/* Sky Notebook — course behaviour. No dependencies. */
(function () {
  "use strict";
  var KEY = "sky-notebook-progress-v1";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* ------------------------------------------------------------ progress */
  function load() {
    try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (_) { return {}; }
  }
  function save(p) {
    try { localStorage.setItem(KEY, JSON.stringify(p)); } catch (_) { /* private mode: progress is session-only */ }
  }
  var progress = load();
  var lessons = $$(".lesson[data-lesson]");
  var total = lessons.length;

  function paint() {
    var done = 0;
    lessons.forEach(function (art) {
      var id = art.dataset.lesson, rec = progress[id];
      var badge = $(".done", art), link = $('.track__list a[data-lesson="' + id + '"]');
      if (rec) {
        done += 1;
        if (badge) badge.hidden = false;
        if (link) link.classList.add("is-done");
      } else {
        if (badge) badge.hidden = true;
        if (link) link.classList.remove("is-done");
      }
    });
    $("#progress-fill").style.width = (total ? (done / total) * 100 : 0) + "%";
    $("#progress-text").textContent = done + " of " + total + " done";
    $("#progress-reset").hidden = done === 0;
  }

  $("#progress-reset").addEventListener("click", function () {
    if (!confirm("Clear your progress on this browser?")) return;
    progress = {};
    save(progress);
    lessons.forEach(function (art) {
      $$(".dial button", art).forEach(function (b) { b.setAttribute("aria-pressed", "false"); });
      $("[data-reveal]", art).disabled = true;
      $(".reveal", art).hidden = true;
      var v = $(".reveal__verdict", art); v.classList.remove("is-right", "is-wrong");
    });
    paint();
  });

  /* ---------------------------------------------------------- the dials */
  lessons.forEach(function (art) {
    var id = art.dataset.lesson;
    var buttons = $$(".dial button", art);
    var revealBtn = $("[data-reveal]", art);
    var reveal = $(".reveal", art);
    var verdict = $(".reveal__verdict", art);
    var correct = reveal.dataset.correct;
    var choice = null;

    function show(ch, animate) {
      reveal.hidden = false;
      revealBtn.textContent = "Answer logged";
      revealBtn.disabled = true;
      verdict.classList.remove("is-right", "is-wrong");
      if (correct !== "any") verdict.classList.add(ch === correct ? "is-right" : "is-wrong");
      buttons.forEach(function (b) { b.disabled = true; });
      if (animate) reveal.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }

    buttons.forEach(function (b) {
      b.setAttribute("aria-pressed", "false");
      b.addEventListener("click", function () {
        choice = b.dataset.choice;
        buttons.forEach(function (o) { o.setAttribute("aria-pressed", String(o === b)); });
        revealBtn.disabled = false;
      });
    });

    revealBtn.addEventListener("click", function () {
      if (!choice) return;
      progress[id] = { choice: choice, at: new Date().toISOString() };
      save(progress);
      show(choice, true);
      paint();
    });

    // restore
    if (progress[id]) {
      var prev = progress[id].choice;
      buttons.forEach(function (o) { o.setAttribute("aria-pressed", String(o.dataset.choice === prev)); });
      choice = prev;
      show(prev, false);
    }
  });
  paint();

  /* ---------------------------------------------- track tab highlighting */
  var tabs = $$(".tracks a");
  var tracks = $$("[data-track]").filter(function (el) { return el.tagName === "SECTION"; });
  function setTab(name) {
    tabs.forEach(function (a) { a.setAttribute("aria-current", String(a.dataset.track === name)); });
  }
  if ("IntersectionObserver" in window) {
    var current = null;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { current = e.target.dataset.track; setTab(current); } });
    }, { rootMargin: "-40% 0px -55% 0px" });
    $$(".lesson, .track").forEach(function (el) {
      var t = el.dataset.track || (el.dataset.lesson && el.dataset.lesson.charAt(0) === "t" ? "transients" : "worlds");
      el.dataset.track = t;
      io.observe(el);
    });
  }

  /* ------------------------------------------------------ hero star field */
  (function stars() {
    var g = $("#stars"); if (!g) return;
    var seed = 7;
    function rnd() { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }
    var out = "";
    for (var i = 0; i < 260; i++) {
      var x = Math.round(rnd() * 1200), y = Math.round(rnd() * 380), r = (rnd() * 1.4 + 0.3).toFixed(2), o = (rnd() * 0.6 + 0.3).toFixed(2);
      out += '<circle cx="' + x + '" cy="' + y + '" r="' + r + '" opacity="' + o + '"/>';
    }
    g.innerHTML = out;
  })();

  /* ------------------------------------------------------- transit demo */
  (function transit() {
    var slider = $("#transit-size"), out = $("#transit-out"), planet = $("#transit-planet"), curve = $("#transit-curve"), label = $("#transit-label");
    if (!slider) return;
    var STAR_R = 46, EARTH_PX = 46 / 109;          // Sun is ~109 Earth radii
    function draw() {
      var re = parseFloat(slider.value);
      var rp = Math.max(1.2, EARTH_PX * re);
      planet.setAttribute("r", rp.toFixed(2));
      var depth = Math.pow(rp / STAR_R, 2);         // fraction of light blocked
      var pct = depth * 100;
      out.value = re.toFixed(1) + " Earth" + (re === 1 ? "" : "s");
      // Light curve: baseline at y=70, dip scaled so 1% depth ≈ 60px, floor so Earth is visible
      var dipPx = Math.max(2, Math.min(110, depth * 6000));
      var x0 = 240, x1 = 600, y0 = 70;
      var tIn = 380, tOut = 460, ramp = 12;
      var d = "M" + x0 + " " + y0 + " L" + (tIn - ramp) + " " + y0 + " L" + tIn + " " + (y0 + dipPx) + " L" + tOut + " " + (y0 + dipPx) + " L" + (tOut + ramp) + " " + y0 + " L" + x1 + " " + y0;
      curve.setAttribute("d", d);
      label.textContent = "dip: " + (pct < 0.01 ? pct.toFixed(4) : pct < 0.1 ? pct.toFixed(3) : pct.toFixed(2)) + "% of the star's light";
    }
    slider.addEventListener("input", draw);
    draw();
  })();
})();
