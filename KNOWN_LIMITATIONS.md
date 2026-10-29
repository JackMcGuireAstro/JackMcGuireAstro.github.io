# Known limitations — CTAS and WorldsIndex

Statement of 2026-10-29 (Track 7). It is released with both products and is part of the
review packet (`review/`). It names what the products do not do or do not yet do, so that a
reader does not have to infer it from silence. Each item says where the evidence is.
Software checks passing, sources being available, data being complete, scientific
validation and peer review are different things; none of them is implied by another.

## Both products

- **No independent scientific review yet.** No representative dossier has been reviewed by
  an outside domain expert (readiness gate 10). The review packet in `review/` is ready;
  reviewers have not been named.
- **Nothing is published or registered.** No DOI, Zenodo, Software Heritage, registry entry
  or announcement (owner's decision, 2026-10-19). `release/README.md` documents the DOI step
  without executing it.
- **No manual screen-reader pass.** Automated accessibility checks (axe-core, WCAG 2.x A/AA)
  run in nine browser suites; the VoiceOver / second screen-reader pass in
  `review/accessibility-manual-pass.md` has not been done (gate 7).
- **Loading budgets.** On a throttled mobile profile the CTAS page's Largest Contentful Paint
  is about 4.5 s (target 2.5 s) and it loads about 206 kB of gzip-compressed JavaScript
  (target 200 kB). The other main pages meet both targets. Measurements:
  `benchmarks/performance/latest.json`.
- **GitHub Pages size.** The deployed site is about 0.77 GB of the 1 GB Pages limit, most of
  it CTAS dossier chunks (about 0.28 GB compressed). See `release/README.md`.
- **Visitor tools** (when landed): the TOM Toolkit broker was tested with stand-ins, not in a
  live TOM; the SQL page's Parquet reader is fetched by DuckDB-WASM from
  extensions.duckdb.org; the notebook link opens the public JupyterLite demo, whose version
  we do not pin.

## CTAS

- **Coverage is still being filled.** Every public event is asked at every applicable source
  newest-first by a sweep that takes months; ATLAS and ASAS-SN cannot be swept under their
  terms. Per-source executed-receipt and data rates are in `benchmarks/sources/SUMMARY.md`
  (for example, broker classifications exist so far only for the events the sweep has
  reached).
- **Provider failures are common and shown, not hidden.** Receipts in the current release
  include about 18,600 provider-failure and 4,600 temporarily-unavailable dispositions; a
  failure is never shown as "no match".
- **Host association** (Track 3c part 2, when landed) is a documented reimplementation over
  galaxies CTAS already holds, not the astro-prost package; redshifts come only from
  retained NED/SIMBAD rows (DESI DR1 and NED-LVS are not ingested).
- **Gravitational waves** (Track 3d, when landed): no live LVK event exists until the next
  observing run (targeted mid-January 2027); the pipeline is verified on a real archived
  sky map. Without an installed NED-LVS/GLADE+ file the galaxy ranking uses the ~95 galaxies
  CTAS holds and says it is incomplete. Treasure Map is not read (its API needs a personal
  token).
- **Instrument values.** WIRO's DoublePrime camera values are measured (r band); WIRO's other
  bands, its obstruction and field of view, every Red Buttes value except the aperture, and
  the spectroscopy thresholds are placeholders and labelled so
  (`tools/exposure/instruments.js`). Run sheets do not list standard stars.
- **Not done:** superbol bolometric curves; cached cutout images; the CTAS half of the
  identity gold set (transient aliases, revised localisations).
- **Light-curve science** fits are phenomenological screens with stated claim boundaries;
  they are not classifications, physical models or distances.

## WorldsIndex

- **Identity.** Identity rules v4 for about 4,600 NASA mission candidates are proposed, not
  applied; 57 rows and 18 gold-set queue rows wait for a person
  (`benchmarks/identity/worldsindex-gold-set.json`). Example: HD 26965 b (40 Eridani A b) is
  listed as controversial with its identity unreviewed (`benchmarks/science/fixtures.json`).
- **Gaia DR4** support switches on only after 2 December 2026 and only for tables a real row
  confirms; the astrometric-candidate table name is a guess and stays off unless confirmed.
  There is no page panel for the DR4 cross-match yet.
- **Calculated quantities** (Track 3b part 1, when landed): mass–radius reference curves use
  the Zeng et al. 2016 analytic fit, not the 2019 tables; limb darkening uses a gray
  Eddington approximation, not Claret tables; TSM/ESM and transit durations are computed only
  where the needed inputs exist (most planets lack them).
- **Not yet held:** NExoList, TEPCat, PlanetS, per-host RV ledgers, mission target lists,
  atmospheric-detection and transit-timing ledgers; no radvel or BLS checks. ExoClock data is
  not used.
- **Live rows are not stored.** Values fetched live in the browser are shown with "new since
  the release" and are not kept (owner's rule: stream, don't store).
