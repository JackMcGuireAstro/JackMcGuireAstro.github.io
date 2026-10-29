# Claim boundaries as published

Each product states what a value or a check does and does not mean. These are quoted from the
files of the releases of 2026-10-29 (ctas-data defb23a8, worldsindex-data cf80c35d); the file or block is named before each quote. Reviewers: check that the pages show these where the values appear, and that no page claims more.

## ctas/data/certification.json — `claim_boundary`

> Automated checksum and structural verification of one static snapshot; not peer review, scientific truth, classification validation, discovery authority, or a managed-service deployment claim.

## ctas/data/source-universe.json — `claim_boundary`

> Maintained CTAS source universe, not a claim that every astronomical source exists here or that every source was queried for every candidate.

## ctas/data/source-accounting.json — `statement`

> The four source denominators per source for this release, summed over every public record's source matrix: declared (the contract is in this release's source universe), applicable (records whose executable applicability rule names the source), executed (persisted query receipts with an executed terminal outcome, and the records that hold at least one), and data-bearing (records whose current query outcome for the source is DATA_RETURNED, beside the records that keep retained rows from it, which also counts rows retained before the receipt model).

## CTAS dossier comparison block (ctas.comparison@1) — `claimBoundary`

> Source assertions shown side by side with stated comparison rules. A consistent label is not proof that the values are scientifically equivalent, an inconsistent label is not a judgement of which source is right, and a selection is a display rule, not a measurement or a review.

## CTAS light-curve science run multiband-bazin — `claimBoundary`

> A phenomenological fit to public difference and forced photometry; it describes the light-curve shape and is not a classification, a physical model or a distance.

## CTAS light-curve science run absolute-magnitude — `claimBoundary`

> A distance-dependent estimate from a phenomenological peak; not a calibrated luminosity.

## CTAS light-curve science run jump-detector — `claimBoundary`

> A brightening relative to the event's own recent detections; not a classification.

## CTAS light-curve science run salt3 — `claimBoundary`

> SALT3 light-curve parameters assuming a normal type Ia supernova; not a classification or a distance.

## CTAS light-curve science run screens — `claimBoundary`

> Screens that point at candidates for a closer look (kilonova-like timescales, nuclear position); they are not classifications.

## CTAS science brief — `claim_boundary`

> A deterministic summary of this checksum-bound public record; it does not confirm discovery, class, counterpart, host, or scientific importance.

## CTAS suggested follow-up — `claim_boundary`

> Heuristic draft from public evidence; not an exposure-time calculation or observing request.

## worldsindex/data/source-coverage.json — `boundary`

> Eligibility means the object states an identifier of the kind the source is keyed on (its own name or host, or one another source states for it); attached means a row reached it by that exact identifier. Missing at provider is the provider's silence for those identifiers, not an absence of the object. Nothing here changes identity or object counts.

## worldsindex/data/manifest.json — `scientificBoundary`

> Static public catalog projection. Source membership is not independent confirmation; source-composite values are not self-consistent publication solutions; catalog projections are not completeness-corrected populations.

## worldsindex/data/catalog-index.json.gz — `identityNotice`

> OEC and Exoplanet.eu records fold into an existing marker only through a unique exact normalized planet-name match plus a host witness: an exact host name, or (Exoplanet.eu) a host name NASA itself states for that object (HD, HIP, TIC, Gaia DR3, KIC, KOI-n, EPIC or TOI-n from the NASA tables) with positions within 60 arcsec. An Exoplanet.eu row whose exact aliases name both a NASA planet and NASA mission candidates folds into the NASA planet only when each candidate is bound to it by the provider's own cross-identifier (KOI kepler_name, K2 pl_name, or TIC) with agreeing position and period (within 3 sigma, 1 % for a planet-level name, 0.1 % for a TIC). A row named exactly by a NASA mission designation (KOI, TOI or K2 candidate number) folds into that candidate when position and period agree. Ambiguous and unreviewed rows remain separate until identity review; catalog agreement is not independent confirmation.

## worldsindex/data/catalog-index.json.gz — `coordinateNotice`

> Markers use each selected source record’s retained right ascension and declination. Alternate catalog coordinates remain separate coordinate claims and are never averaged.
