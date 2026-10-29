# What to check

Questions follow the professional readiness gates (`framework/PROFESSIONAL_READINESS_GATES.md`
in the project). For each, write what you found, what you did not check, and why.

## Identity and association (gate 1)
- Does each record's name, aliases and source identifiers agree with the sources it links?
- Are coordinate-only matches shown as candidates, and ambiguous names as several candidates?
- HD 26965 b: is the controversial listing and the unreviewed identity shown plainly?

## Sources and receipts (gates 0 and 2)
- For a record, does every applicable source show a terminal state (data, searched-no-match,
  failure, blocked, not queried …), with a time?
- Is a provider failure ever presented as "no match"? (SN2026aabx has one.)
- Do the per-source counts on the status pages match `benchmarks/sources/SUMMARY.md`?

## Conflicts and selections (gate 4)
- SN2026aabx: are both classifications visible, marked inconsistent, neither averaged nor
  hidden? Is the headline value labelled a selection with its rule?
- HD 209458 b: does every published solution appear as its own row with its reference?
- Is there any place where values from different sources were combined into one number?

## Units, epochs, time systems, frames (gate 3)
- Are original and normalized units both available? Are limits distinguished from values in
  text and shape, not colour alone?
- Do epochs say their time system (BJD_TDB, UTC, MJD) and frame (ICRS)?

## Methods and analysis (gate 5)
- Light-curve fits, calculated planet quantities, host association, anomaly scores: is the
  method, version, inputs and claim boundary shown, and "not computed" given with a reason
  rather than a default?
- Run the two reproducibility notebooks (`repro/*/reproduce.ipynb`); do they reproduce?

## Citations and rights (gate 3)
- Does each value link to its reference (bibcode, TNS report, circular, catalog)?
- Are acknowledgment texts present for sources that require them (brokers, catalogs)?

## Follow-up (gate 6)
- Are site, time window, Moon and twilight assumptions stated? Are placeholder instrument
  values labelled?

## Accessibility (gate 7)
- With keyboard only: search, open a record, inspect a conflict, export. Is focus visible
  and does Tab follow what you see?
- With a screen reader: see `accessibility-manual-pass.md`.

## Exports (gate 8)
- Open a VOTable or ECSV export in TOPCAT or astropy: units, limits, identifiers and
  provenance intact? Does the export carry its manifest and citations?
