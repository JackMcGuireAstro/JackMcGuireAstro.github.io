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

console.log("catalog model: 21 assertions passed");
