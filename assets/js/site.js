/* ==========================================================================
   jackmcguireastro.github.io, shared behaviour
   Mobile navigation plus an optional, browser-local QualQuest resume card.
   The site and study link remain fully usable with JavaScript disabled.
   ========================================================================== */
(function () {
  "use strict";

  // Signal to CSS that JS is available (the no-JS fallback shows the nav open).
  document.documentElement.classList.remove("no-js");

  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("primary-nav");

  if (toggle && nav) {
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
  }

  var qualquestCard = document.querySelector("[data-qualquest-card]");
  if (!qualquestCard) return;

  var totalNode = document.querySelector("[data-qualquest-total]");
  var parsedTotal = totalNode ? Number(totalNode.textContent.replace(/[^0-9]/g, "")) : 0;
  var totalProblems = parsedTotal > 0 ? parsedTotal : 7936;
  var savedProgress = null;
  var returningCookie = false;

  function applyTotalProblems(nextTotal) {
    var parsed = Number(nextTotal);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    totalProblems = Math.floor(parsed);
    if (totalNode) totalNode.textContent = totalProblems.toLocaleString("en-US");

    var liveProgressBar = document.querySelector("[data-qualquest-progress]");
    if (liveProgressBar) {
      liveProgressBar.setAttribute("aria-valuemax", String(totalProblems));
      liveProgressBar.setAttribute("aria-valuenow", String(Math.min(totalProblems, mastered || 0)));
    }
    if (!isReturning) return;

    var liveHeading = document.querySelector("[data-qualquest-heading]");
    var liveMeter = document.querySelector("[data-qualquest-meter]");
    if (liveHeading && mastered > 0) {
      liveHeading.textContent = mastered.toLocaleString("en-US") + " of "
        + totalProblems.toLocaleString("en-US") + " questions mastered.";
    }
    if (liveMeter) {
      var livePercent = Math.min(100, (mastered / totalProblems) * 100);
      liveMeter.style.width = mastered > 0 ? Math.max(1.25, livePercent) + "%" : "0";
    }
  }

  applyTotalProblems(totalProblems);
  fetch("qualquest/data/stats.json", { cache: "no-store" })
    .then(function (response) {
      if (!response.ok) throw new Error("QualQuest stats unavailable");
      return response.json();
    })
    .then(function (stats) { applyTotalProblems(stats.missionProblems); })
    .catch(function () { /* Keep the audited count embedded in the page. */ });

  try {
    returningCookie = document.cookie.split(";").some(function (value) {
      return value.trim() === "qualquest_returning=1";
    });
  } catch (error) {
    returningCookie = false;
  }

  try {
    var savedValue = window.localStorage.getItem("qualquest-public-progress-v1");
    if (savedValue) savedProgress = JSON.parse(savedValue);
  } catch (error) {
    savedProgress = null;
  }

  var entries = savedProgress && savedProgress.entries && typeof savedProgress.entries === "object"
    ? Object.keys(savedProgress.entries).map(function (key) { return savedProgress.entries[key]; })
    : [];
  var mastered = entries.filter(function (entry) {
    return entry && (entry.completed === true || Number(entry.stage) >= 3);
  }).length;
  var attempted = entries.filter(function (entry) {
    return entry && Number(entry.stage) > 0;
  }).length;
  var xp = savedProgress && Number.isFinite(Number(savedProgress.xp))
    ? Math.max(0, Math.floor(Number(savedProgress.xp)))
    : 0;
  var sessions = savedProgress && Number.isFinite(Number(savedProgress.sessions))
    ? Math.max(0, Math.floor(Number(savedProgress.sessions)))
    : 0;
  var isReturning = returningCookie || mastered > 0 || attempted > 0 || xp > 0 || sessions > 0;

  if (!isReturning) return;

  document.querySelectorAll("[data-qualquest-cta]").forEach(function (link) {
    link.textContent = "Continue QualQuest";
  });

  var kicker = document.querySelector("[data-qualquest-kicker]");
  var heading = document.querySelector("[data-qualquest-heading]");
  var meta = document.querySelector("[data-qualquest-meta]");
  var meter = document.querySelector("[data-qualquest-meter]");
  var progressBar = document.querySelector("[data-qualquest-progress]");

  if (kicker) kicker.textContent = "Welcome back";
  if (heading) {
    if (mastered > 0) {
      heading.textContent = mastered.toLocaleString("en-US") + " of "
        + totalProblems.toLocaleString("en-US") + " questions mastered.";
    } else if (attempted > 0 || xp > 0 || sessions > 0) {
      heading.textContent = "Your physics campaign is underway.";
    } else {
      heading.textContent = "Your next QualQuest mission is ready.";
    }
  }
  if (meta) {
    meta.textContent = xp + " XP · " + sessions + (sessions === 1 ? " session" : " sessions")
      + " · saved privately in this browser";
  }
  if (meter) {
    var percent = Math.min(100, (mastered / totalProblems) * 100);
    meter.style.width = mastered > 0 ? Math.max(1.25, percent) + "%" : "0";
  }
  if (progressBar) progressBar.setAttribute("aria-valuenow", String(Math.min(totalProblems, mastered)));
  qualquestCard.setAttribute("data-returning", "true");
})();
