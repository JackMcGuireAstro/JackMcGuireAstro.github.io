"use strict";

const assert = require("assert");
const model = require("../ctas/catalog-model.js");

const now = Date.parse("2026-08-23T12:00:00Z");
function candidate(overrides) {
  return Object.assign({
    name: "AT2026abc",
    discovery_time: "2026-08-22T00:00:00Z",
    updated_at: "2026-08-23T00:00:00Z",
    ra_deg: 20,
    dec_deg: -10,
    classification: "Unclassified",
    primary_messenger: "electromagnetic",
    messenger_channels: ["electromagnetic"],
    discovery_magnitude: 17,
    follow_up_total: 0,
    follow_up_counts: {spectra: 0},
    record_completeness: {label: "Event record only"}
  }, overrides || {});
}

const catalog = Array.from({length: 220}, (_, index) => candidate({name: "AT" + index}));
catalog[200] = candidate({name: "BeyondFirstPage", latest_spectrum_at: "2026-08-10T00:00:00Z", follow_up_total: 1,
  follow_up_counts: {spectra: 1}, record_completeness: {label: "Rich public record"}});

assert.strictEqual(catalog.filter(c => model.matchesPreset(c, "all", now)).length, 220);
assert.strictEqual(catalog.filter(c => model.matchesPreset(c, "spectra", now)).length, 1,
  "new-spectra preset must inspect the complete catalog, beyond the first rendered page");
assert.strictEqual(model.matchesPreset(candidate(), "event-only", now), true);
assert.strictEqual(model.matchesPreset(candidate({discovery_time: "2026-08-23T11:00:00Z"}), "today", now), true);
assert.strictEqual(model.matchesPreset(candidate({discovery_time: "2026-08-22T11:59:59Z"}), "today", now), false);
assert.strictEqual(model.matchesPreset(candidate({follow_up_total: 1}), "event-only", now), false);
assert.strictEqual(model.matchesPreset(candidate({primary_messenger: "multimessenger"}), "multimessenger", now), true);
assert.strictEqual(model.matchesPreset(candidate({messenger_channels: ["neutrino", "gamma-ray"]}), "multimessenger", now), true);
assert.strictEqual(model.matchesPreset(candidate({follow_up_counts: {spectra: 1}}), "no-spectra", now), false);
assert.strictEqual(model.matchesPreset(candidate({latest_retraction_at: "2026-08-22T00:00:00Z"}), "retracted", now), true);
assert.strictEqual(model.matchesPreset(candidate({latest_retraction_at: "2026-06-22T00:00:00Z"}), "retracted", now), true);
assert.strictEqual(model.matchesPreset(candidate({status: "retracted", latest_retraction_at: null}), "retracted", now), true,
  "provider-retracted records remain discoverable even when the source supplied no explicit retraction clock");
assert.strictEqual(model.matchesPreset(candidate({follow_up_total: 4, follow_up_counts: {spectra: 1}, classification: "SN Ia"}), "needs-follow-up", now), false);
assert.strictEqual(model.matchesPreset(candidate({follow_up_total: 4, follow_up_counts: {spectra: 0}, classification: "SN Ia"}), "needs-follow-up", now), true);

const sky = [
  candidate({name: "week", discovery_time: "2026-08-22T00:00:00Z"}),
  candidate({name: "month", discovery_time: "2026-08-10T00:00:00Z"}),
  candidate({name: "old", discovery_time: "2026-06-10T00:00:00Z"}),
  candidate({name: "no-coordinates", ra_deg: null, dec_deg: null})
];
assert.deepStrictEqual(model.skyCandidates(sky, 7, now).map(c => c.name), ["week"]);
assert.deepStrictEqual(model.skyCandidates(sky, 30, now).map(c => c.name), ["week", "month"]);

assert.strictEqual(model.sexagesimal(359.999999, 89.999999), "00:00:00.0 +90:00:00",
  "rounding must carry without emitting a :60 component");
assert.strictEqual(model.sexagesimal(15, -0), "01:00:00.0 -00:00:00");
assert.strictEqual(model.sexagesimal(20, -10), "01:20:00.0 -10:00:00");
assert.strictEqual(model.sexagesimal(360, 0), "", "out-of-range ICRS coordinates are refused");

const bootstrapColumns = [
  "event_id", "name", "ctas_score", "follow_up_total", "detail_chunk",
  "n_classifications", "n_classification_history", "n_observations", "n_spectra",
  "n_messenger_signals", "n_publications", "n_publication_revisions", "n_host_context", "n_catalog_counterparts", "n_archive_products",
  "record_label", "record_present", "record_applicable", "record_not_assessed", "record_fraction",
  "primary_source_key", "primary_source_url", "primary_source_designation", "identity_state", "conflict_count",
  "source_declared", "source_applicable", "source_executed", "source_data_bearing"
];
const bootstrapValues = {
  event_id: "123e4567-e89b-42d3-a456-426614174000", name: "AT2026columnar", ctas_score: 72,
  follow_up_total: 4, detail_chunk: "candidate-chunks/7f.json", n_classifications: 1,
  n_classification_history: 0, n_observations: 2, n_spectra: 1, n_messenger_signals: 0,
  n_publications: 0, n_host_context: 0, n_catalog_counterparts: 0, n_archive_products: 0,
  n_publication_revisions: 0,
  record_label: "Partial public record", record_present: 4, record_applicable: 7,
  record_not_assessed: 1, record_fraction: 4 / 7, primary_source_key: "tns",
  primary_source_url: "https://www.wis-tns.org/object/2026columnar", primary_source_designation: "AT2026columnar",
  identity_state: "RESOLVED", conflict_count: 2, source_declared: 14, source_applicable: 5,
  source_executed: 3, source_data_bearing: 2
};
const inflated = model.inflateBootstrap({candidate_columns: bootstrapColumns,
  candidate_rows: [bootstrapColumns.map(column => bootstrapValues[column])]});
assert.strictEqual(inflated.length, 1);
assert.deepStrictEqual(inflated[0].follow_up_counts,
  {classifications: 1, classification_history: 0, observations: 2, spectra: 1,
    messenger_signals: 0, publications: 0, publication_revisions: 0,
    host_context: 0, catalog_counterparts: 0, archive_products: 0});
assert.deepStrictEqual(inflated[0].identity_resolution, {state: "RESOLVED"});
assert.deepStrictEqual(inflated[0].source_accounting,
  {declaredSources: 14, applicableSources: 5, executedQueryReceipts: 3, dataBearingSources: 2});
assert.throws(() => model.inflateBootstrap({candidate_columns: bootstrapColumns, candidate_rows: [["too short"]]}),
  /wrong width/);
assert.throws(() => model.inflateBootstrap({candidate_columns: ["event_id", "event_id"], candidate_rows: []}),
  /no valid candidate table/);

const terminalScenario = model.scoreScenario({baseline: 35, terms: [{code: "recency_points", points: 20}],
  multimessenger_bonus: 5, status_override: "retracted"}, {recency_points: 50});
assert.strictEqual(terminalScenario.final_preclip, 90);
assert.strictEqual(terminalScenario.final_score, 0, "terminal status override must be applied after every arithmetic term");
const roundedScenario = model.scoreScenario({baseline: 35, terms: [], multimessenger_bonus: 0,
  persisted_factor_rounding_residual: 0.01, status_override: null}, {});
assert.strictEqual(roundedScenario.persisted_factor_rounding_residual, 0.01);
assert.strictEqual(roundedScenario.final_score, 35.01);

const replayTimeline = [
  {entry_id: "first", public_available_at: "2026-08-20T01:00:00Z"},
  {entry_id: "future", public_available_at: "2026-08-20T03:00:00Z"},
  {entry_id: "undated", public_available_at: null}
];
const replayBefore = model.evidenceAt(replayTimeline, "2026-08-20T00:59:59Z");
assert.deepStrictEqual(replayBefore.visible, []);
assert.deepStrictEqual(replayBefore.undated.map(row => row.entry_id), ["undated"]);
const replayBetween = model.evidenceAt(replayTimeline, "2026-08-20T02:00:00Z");
assert.deepStrictEqual(replayBetween.visible.map(row => row.entry_id), ["first"],
  "historical replay must exclude evidence arriving after the requested cutoff");

const history = Array.from({length: 9}, (_, index) => ({
  published_at: "2026-08-23T" + String(18 - index).padStart(2, "0") + ":00:00Z",
  catalog_content_checksum_sha256: String(index).repeat(64),
  added_count: 0,
  removed_count: 0
}));
history[8] = Object.assign({}, history[8], {added_count: 81, evidence: "checksum-bound TNS batch"});
const visibleHistory = model.releaseHistorySelection(history, 6, 8);
assert.strictEqual(visibleHistory.length, 7);
assert.strictEqual(visibleHistory[6].added_count, 81,
  "a documented material intake remains visible after routine releases exceed the recent preview");

console.log("catalog model: columnar bootstrap, filters, score, replay, sky, and history assertions passed");
