# jackmcguireastro.github.io

Jack McGuire's website: research, publications, and two astronomy resources built on it.

- `index.html` — homepage (research, publications, about, contact).
- `worldsindex/` — WorldsIndex, a catalog of worlds beyond the Solar System.
- `ctas.html`, `ctas/` — CTAS, the Cowboy Transient Alert System.
- `tonight/`, `tools/` — observing planner and tools (finder, exposure, coordinates, SQL, cone search, notebook, follow).
- `research/` — research pages; `assets/presentations/` — talks, posters and reports (PDF).

Plain static HTML, CSS and JavaScript served by GitHub Pages; no build step for the pages.
Every page shares one header and footer (`assets/partials/`), kept in step by
`python3 scripts/sync_chrome.py`. Release data for CTAS and WorldsIndex lives on the
`ctas-data` and `worldsindex-data` branches; see `CTAS-AUTOMATION.md`,
`WORLDSINDEX-AUTOMATION.md` and `RESILIENCE.md` for how releases are built, checked and deployed.

`LICENSE.txt` is the Creative Commons Attribution 3.0 licence the site's original HTML5 UP
template came with.
