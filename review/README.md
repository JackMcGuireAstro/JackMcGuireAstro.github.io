# Review packet — CTAS and WorldsIndex

Prepared 2026-10-29 for two independent reviewers (readiness gate 10). Nothing in this
packet has been sent to anyone; reviewers have not yet been named. The packet asks for a
domain review of representative records, not of the software.

## What the two products claim

- **CTAS** (Cowboy Transient Alert System, https://jackmcguireastro.github.io/ctas.html)
  collects public transient reports (optical transients, gamma-ray, X-ray, neutrino, fast
  radio burst and gravitational-wave notices) about every half hour, keeps every source's
  assertion and query receipt, shows disagreements without averaging, and ranks follow-up
  candidates for WIRO and Red Buttes. It does not classify transients itself, trigger
  telescopes, or claim completeness beyond the receipts it shows.
- **WorldsIndex** (https://jackmcguireastro.github.io/worldsindex/) assembles exoplanet
  records from 42 declared sources hourly, keeps every published solution as its own row,
  separates selections from measurements, and propagates transit-ephemeris uncertainty.
  It does not average across catalogs or decide which catalog is right.

The exact published wording of every claim boundary is in `claim-boundaries.md`. What the
products do not do is in `../KNOWN_LIMITATIONS.md`.

## Representative records (open these)

| Product | Record | Why it is in the packet | Link |
|---|---|---|---|
| CTAS | SN2026aabx | two disagreeing classifications (Nova vs SN Ia) and a failed provider | https://jackmcguireastro.github.io/ctas.html?event=3e79a709-0c73-45a2-bcc7-c64c18bd9b98#dossier |
| CTAS | GRB 260822A | a high-energy event whose evidence timeline keeps a retraction | https://jackmcguireastro.github.io/ctas.html?event=1040bcb8-ef58-4f1f-9a0c-08f7cf6ba867#dossier |
| CTAS | SN2025bbe | a multi-band light-curve fit, reproduced outside the website (`../repro/ctas/`) | https://jackmcguireastro.github.io/ctas.html?alias=SN2025bbe#dossier |
| WorldsIndex | HD 209458 b | reference hot Jupiter: 131 source rows, conflict sets, ephemeris reproduced (`../repro/worldsindex/`) | https://jackmcguireastro.github.io/worldsindex/?object=object-hd-209458-b&section=object |
| WorldsIndex | HD 26965 b (40 Eridani A b) | disputed planet: listed as controversial, identity unreviewed | https://jackmcguireastro.github.io/worldsindex/?object=object-oec-7db63939d648877516fc&section=object |

The facts each record shows, and what a reviewer should expect to see, are recorded in
`../benchmarks/science/fixtures.json` (with the SHA-256 of each record as published).
Exports to try from each page: the record's JSON download, the Compare-sources ECSV/VOTable
export (CTAS) or the Compare-tab export (WorldsIndex), and the research tables (CTAS
Downloads, WorldsIndex Sources → Downloads). Each export carries a manifest and citations.

## What to check

`what-to-check.md` lists the questions per area: identity, source completeness and receipts,
conflicts and selections, units, epochs and time systems, methods, citations and rights,
analysis, accessibility, exports. Please answer what you can and say what you did not check.

## How to report findings

Open one GitHub issue per finding with the "Review finding" form
(https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/issues/new?template=review-finding.yml),
or send them in one document; each finding is tracked as an issue and closed only with the
change that resolves it, or with a written reason it stays.

## Evidence files

- Source accounting with the four denominators per source: `../benchmarks/sources/SUMMARY.md`
- Identity gold-set results: `../benchmarks/identity/worldsindex-gold-set.json`
- Page loading budgets: `../benchmarks/performance/latest.json`
- Backup-and-restore drill: `../benchmarks/recovery/`
- Reproducibility bundles: `../repro/ctas/`, `../repro/worldsindex/`
- Accessibility: automated axe checks run in every browser suite; the manual pass is
  `accessibility-manual-pass.md` (not yet done)
- Release record of each deployment: `https://jackmcguireastro.github.io/release/manifest.json`
