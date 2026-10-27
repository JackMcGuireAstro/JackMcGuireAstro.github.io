// ctas/comparison.js in a bare JavaScript runtime (Track 2b): the comparison panel and the
// brief's link drawn from a block written by scripts/ctas_comparison.py (the fixture below is its
// output for a TNS hourly row, a TNS public-detail row 3.3 arcsec away and two disagreeing
// classes), both absent without the block; the exports of selected rows: ECSV readable by
// astropy, VOTable 1.4, and the manifest whose SHA-256 checksums cover exactly those files.
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const path = require("node:path");

const comparison = require(path.join(__dirname, "..", "ctas", "comparison.js"));
let passed = 0;
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const BLOCK = {
 "schema": "ctas.comparison@1",
 "policyVersion": "ctas-comparison-policy.v1",
 "sigmaThreshold": 3.0,
 "rules": {
  "sigma": "Two values that both state an uncertainty are inconsistent when they differ by more than 3 combined standard deviations (uncertainties added in quadrature; a stated position error radius is treated as one standard deviation).",
  "position": "Two positions with no stated uncertainty are inconsistent when they are more than 2 arcsec apart; when one states an error radius, the separation is compared with 3 times that radius.",
  "redshift": "Two redshifts with no stated uncertainty are inconsistent when they differ by more than 0.005 (about 1,500 km/s); transient and host redshifts are different definitions and are listed, not compared; host redshifts of differently named hosts are an identity ambiguity.",
  "classification": "Spectroscopic classes and alert or messenger labels are different definitions and are not compared; two active classes differ when neither label refines the other (for example 'SN Ia' and 'SN Ia-91T' agree); an earlier value from the same provider is a revision.",
  "host": "Two proposed hosts agree when their names match after case and spacing are ignored or one name is among the other's NED cross-identifications; otherwise they are an identity ambiguity. Association probabilities are not computed.",
  "photometry": "Only exactly compatible photometry (same epoch, band, system, method, calibration and direct/difference state, two or more providers) is compared; at most 10 inconsistent groups per event are listed, most significant first.",
  "revision": "A retracted row, or a superseded row of the same provider whose value differs beyond the property's rule, is a revision or retraction; a revision within the rule is consistent; the earlier value stays visible."
 },
 "claimBoundary": "Source assertions shown side by side with stated comparison rules. A consistent label is not proof that the values are scientifically equivalent, an inconsistent label is not a judgement of which source is right, and a selection is a display rule, not a measurement or a review.",
 "summary": {
  "properties": 3,
  "rows": 6,
  "propertiesInConflict": 2,
  "conflictCodes": [
   "classification",
   "position"
  ],
  "selections": 3,
  "selectionsDifferingFromDisplay": 0
 },
 "properties": [
  {
   "code": "position",
   "label": "Position",
   "unit": "deg",
   "rule": "Two positions with no stated uncertainty are inconsistent when they are more than 2 arcsec apart; when one states an error radius, the separation is compared with 3 times that radius.",
   "rows": [
    {
     "id": "cmp:2fc22319d703fe54f7e1bcca",
     "sourceId": "tns",
     "sourceRecordId": "tns-hourly:2026zfw",
     "assertionId": null,
     "value": "11.0606556, -25.1085945",
     "normalized": [
      11.0606556,
      -25.1085945
     ],
     "unit": "deg",
     "errPlus": null,
     "errMinus": null,
     "limitType": "NONE",
     "probability": null,
     "definitionCode": "reported-position",
     "epoch": {
      "time": "2026-08-19T08:09:17Z",
      "kind": "provider-modified",
      "frame": "ICRS J2000"
     },
     "method": "TNS public objects (hourly)",
     "instrument": null,
     "reference": {
      "label": "TNS object page; discovery report",
      "url": "https://ui.adsabs.harvard.edu/abs/2026TNSTR3377....1T/abstract",
      "bibcode": "2026TNSTR3377....1T",
      "bibcodes": [
       "2026TNSTR3377....1T"
      ]
     },
     "state": "active",
     "seenInEnvelopes": 1,
     "receipt": {
      "kind": "alert-envelope",
      "envelopeId": "env-tns-2026-08-19 08:09:17",
      "checksumSha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "topic": "topic-tns",
      "receivedAt": "2026-08-19T08:09:17Z"
     },
     "compat": {
      "against": null,
      "relation": null,
      "verdict": "reference",
      "sigma": null,
      "reason": "The reference row for this table (the first active row).",
      "consistent": 0,
      "inconsistent": 1,
      "not-comparable": 0
     }
    },
    {
     "id": "cmp:922e9e0988e98602f973e431",
     "sourceId": "tns-public-reports",
     "sourceRecordId": "tns-detail:2026zfw",
     "assertionId": null,
     "value": "11.0616556, -25.1085945",
     "normalized": [
      11.0616556,
      -25.1085945
     ],
     "unit": "deg",
     "errPlus": null,
     "errMinus": null,
     "limitType": "NONE",
     "probability": null,
     "definitionCode": "reported-position",
     "epoch": {
      "time": "2026-08-20T00:00:00Z",
      "kind": "retrieved",
      "frame": "ICRS J2000"
     },
     "method": "TNS 2.0 public object detail",
     "instrument": null,
     "reference": {
      "label": "TNS public object detail",
      "url": "https://www.wis-tns.org/object/2026zfw",
      "bibcode": null,
      "bibcodes": []
     },
     "state": "active",
     "seenInEnvelopes": 1,
     "receipt": {
      "kind": "alert-envelope",
      "envelopeId": "env-tns-public-reports-2026-08-20 00:00:00",
      "checksumSha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "topic": "topic-tns-public-reports",
      "receivedAt": "2026-08-20T00:00:00Z"
     },
     "compat": {
      "against": 0,
      "relation": "statistically-inconsistent",
      "verdict": "inconsistent",
      "sigma": null,
      "reason": "3.26 arcsec apart with no stated uncertainty (tolerance 2 arcsec).",
      "consistent": 0,
      "inconsistent": 1,
      "not-comparable": 0
     }
    }
   ],
   "rowsOmitted": 0,
   "set": {
    "id": "cmp:9e2a48e55952551174e5e99b",
    "relationLabel": "statistically-inconsistent",
    "conflict": true,
    "rowCount": 2,
    "sourceIds": [
     "tns",
     "tns-public-reports"
    ],
    "definitionCodes": [
     "reported-position"
    ],
    "sigmaThreshold": 3.0,
    "maxPairwiseSigma": null,
    "pairCounts": {
     "total": 1,
     "byRelation": {
      "statistically-inconsistent": 1
     },
     "byVerdict": {
      "consistent": 0,
      "inconsistent": 1,
      "not-comparable": 0
     }
    },
    "notablePairs": [
     [
      0,
      1,
      null,
      "statistically-inconsistent"
     ]
    ],
    "explanation": "The sources disagree under the stated rule: 3.26 arcsec apart with no stated uncertainty (tolerance 2 arcsec).",
    "comparisonPolicyVersion": "ctas-comparison-policy.v1"
   },
   "selections": [
    {
     "id": "cmp:b9b76345c4a20d47b9531b04",
     "ruleId": "ctas-headline",
     "ruleVersion": "v1",
     "rule": "The most recent TNS hourly-feed position (the IAU Transient Name Server is the naming authority); otherwise the GCN localization with the smallest stated error radius; otherwise none.",
     "selector": "exporter",
     "reviewState": "unreviewed",
     "propertyCode": "position",
     "definitionCode": "reported-position",
     "basis": "naming-authority",
     "label": "selection, not a measurement",
     "value": [
      11.0606556,
      -25.1085945
     ],
     "displayValue": "11.0606556, -25.1085945",
     "unit": "deg",
     "errPlus": null,
     "errMinus": null,
     "chosenRow": 0,
     "chosenSourceRecordIds": [
      "tns-hourly:2026zfw"
     ],
     "rejectedRowCount": 1,
     "matchesDisplayedValue": true,
     "reason": "Chosen by ctas-headline.v1 (naming-authority); the source rows are unchanged."
    }
   ]
  },
  {
   "code": "classification",
   "label": "Classification",
   "unit": null,
   "rule": "Spectroscopic classes and alert or messenger labels are different definitions and are not compared; two active classes differ when neither label refines the other (for example 'SN Ia' and 'SN Ia-91T' agree); an earlier value from the same provider is a revision.",
   "rows": [
    {
     "id": "cmp:cac636b64bc0f82a39512238",
     "sourceId": "tns",
     "sourceRecordId": "env-cls-SN Ia-2026-08-20 00:00:00",
     "assertionId": "ca-tns-SN Ia-2026-08-20 00:00:00",
     "value": "SN Ia",
     "normalized": "sn ia",
     "unit": null,
     "errPlus": null,
     "errMinus": null,
     "limitType": "NONE",
     "probability": null,
     "definitionCode": "spectroscopic-class",
     "epoch": {
      "time": "2026-08-20T00:00:00Z",
      "kind": "asserted",
      "frame": null
     },
     "method": null,
     "instrument": null,
     "reference": {
      "label": "TNS classification report",
      "url": "https://ui.adsabs.harvard.edu/abs/2026TNSCR3448....1S/abstract",
      "bibcode": "2026TNSCR3448....1S",
      "bibcodes": [
       "2026TNSCR3448....1S"
      ]
     },
     "state": "active",
     "seenInEnvelopes": 1,
     "receipt": {
      "kind": "alert-envelope",
      "envelopeId": "env-cls-SN Ia-2026-08-20 00:00:00",
      "checksumSha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "topic": "tns_public_objects_hourly",
      "receivedAt": "2026-08-20T00:00:00Z"
     },
     "compat": {
      "against": null,
      "relation": null,
      "verdict": "reference",
      "sigma": null,
      "reason": "The reference row for this table (the first active row).",
      "consistent": 0,
      "inconsistent": 1,
      "not-comparable": 0
     }
    },
    {
     "id": "cmp:278f3a2e0d677adbf869af5f",
     "sourceId": "tns-public-reports",
     "sourceRecordId": "env-cls-SN II <b>-2026-08-21 00:00:00",
     "assertionId": "ca-tns-public-reports-SN II <b>-2026-08-21 00:00:00",
     "value": "SN II <b>",
     "normalized": "sn ii <b>",
     "unit": null,
     "errPlus": null,
     "errMinus": null,
     "limitType": "NONE",
     "probability": null,
     "definitionCode": "spectroscopic-class",
     "epoch": {
      "time": "2026-08-21T00:00:00Z",
      "kind": "asserted",
      "frame": null
     },
     "method": null,
     "instrument": null,
     "reference": {
      "label": "TNS object page",
      "url": "https://www.wis-tns.org/object/2026zfw",
      "bibcode": null,
      "bibcodes": []
     },
     "state": "active",
     "seenInEnvelopes": 1,
     "receipt": {
      "kind": "alert-envelope",
      "envelopeId": "env-cls-SN II <b>-2026-08-21 00:00:00",
      "checksumSha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "topic": "tns_public_objects_hourly",
      "receivedAt": "2026-08-21T00:00:00Z"
     },
     "compat": {
      "against": 0,
      "relation": "status-conflict",
      "verdict": "inconsistent",
      "sigma": null,
      "reason": "Active classes differ ('SN Ia' vs 'SN II <b>').",
      "consistent": 0,
      "inconsistent": 1,
      "not-comparable": 0
     }
    }
   ],
   "rowsOmitted": 0,
   "set": {
    "id": "cmp:979748d8271c9da765ec6e25",
    "relationLabel": "status-conflict",
    "conflict": true,
    "rowCount": 2,
    "sourceIds": [
     "tns",
     "tns-public-reports"
    ],
    "definitionCodes": [
     "spectroscopic-class"
    ],
    "sigmaThreshold": 3.0,
    "maxPairwiseSigma": null,
    "pairCounts": {
     "total": 1,
     "byRelation": {
      "status-conflict": 1
     },
     "byVerdict": {
      "consistent": 0,
      "inconsistent": 1,
      "not-comparable": 0
     }
    },
    "notablePairs": [
     [
      0,
      1,
      null,
      "status-conflict"
     ]
    ],
    "explanation": "The sources disagree under the stated rule: Active classes differ ('SN Ia' vs 'SN II <b>').",
    "comparisonPolicyVersion": "ctas-comparison-policy.v1"
   },
   "selections": [
    {
     "id": "cmp:9c7d0cd0b5849afd6c914bf3",
     "ruleId": "ctas-headline",
     "ruleVersion": "v1",
     "rule": "The most recent active TNS spectroscopic classification; otherwise none (alert and messenger labels are not promoted to a class).",
     "selector": "exporter",
     "reviewState": "unreviewed",
     "propertyCode": "classification",
     "definitionCode": "spectroscopic-class",
     "basis": "naming-authority",
     "label": "selection, not a measurement",
     "value": "sn ia",
     "displayValue": "SN Ia",
     "unit": null,
     "errPlus": null,
     "errMinus": null,
     "chosenRow": 0,
     "chosenSourceRecordIds": [
      "env-cls-SN Ia-2026-08-20 00:00:00"
     ],
     "rejectedRowCount": 1,
     "matchesDisplayedValue": true,
     "reason": "Chosen by ctas-headline.v1 (naming-authority); the source rows are unchanged."
    }
   ]
  },
  {
   "code": "redshift",
   "label": "Redshift",
   "unit": "1",
   "rule": "Two redshifts with no stated uncertainty are inconsistent when they differ by more than 0.005 (about 1,500 km/s); transient and host redshifts are different definitions and are listed, not compared; host redshifts of differently named hosts are an identity ambiguity.",
   "rows": [
    {
     "id": "cmp:785e386a4752b5bb833af76a",
     "sourceId": "tns",
     "sourceRecordId": "tns-hourly:2026zfw",
     "assertionId": null,
     "value": "0.054131",
     "normalized": 0.054131,
     "unit": "1",
     "errPlus": null,
     "errMinus": null,
     "limitType": "NONE",
     "probability": null,
     "definitionCode": "transient-redshift",
     "epoch": {
      "time": "2026-08-19T08:09:17Z",
      "kind": "provider-modified",
      "frame": null
     },
     "method": "TNS public objects (hourly)",
     "instrument": null,
     "reference": {
      "label": "TNS object page",
      "url": "https://www.wis-tns.org/object/2026zfw",
      "bibcode": null,
      "bibcodes": []
     },
     "state": "active",
     "seenInEnvelopes": 1,
     "receipt": {
      "kind": "alert-envelope",
      "envelopeId": "env-tns-2026-08-19 08:09:17",
      "checksumSha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "topic": "topic-tns",
      "receivedAt": "2026-08-19T08:09:17Z"
     },
     "compat": {
      "against": null,
      "relation": null,
      "verdict": "reference",
      "sigma": null,
      "reason": "The reference row for this table (the first active row).",
      "consistent": 1,
      "inconsistent": 0,
      "not-comparable": 0
     }
    },
    {
     "id": "cmp:5b781a22473a46259df27bcd",
     "sourceId": "tns-public-reports",
     "sourceRecordId": "tns-detail:2026zfw",
     "assertionId": null,
     "value": "0.0545",
     "normalized": 0.0545,
     "unit": "1",
     "errPlus": null,
     "errMinus": null,
     "limitType": "NONE",
     "probability": null,
     "definitionCode": "transient-redshift",
     "epoch": {
      "time": "2026-08-20T00:00:00Z",
      "kind": "retrieved",
      "frame": null
     },
     "method": "TNS 2.0 public object detail",
     "instrument": null,
     "reference": {
      "label": "TNS object page",
      "url": "https://www.wis-tns.org/object/2026zfw",
      "bibcode": null,
      "bibcodes": []
     },
     "state": "active",
     "seenInEnvelopes": 1,
     "receipt": {
      "kind": "alert-envelope",
      "envelopeId": "env-tns-public-reports-2026-08-20 00:00:00",
      "checksumSha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "topic": "topic-tns-public-reports",
      "receivedAt": "2026-08-20T00:00:00Z"
     },
     "compat": {
      "against": 0,
      "relation": null,
      "verdict": "consistent",
      "sigma": null,
      "reason": "Differ by 0.00037 (within the 0.005 tolerance).",
      "consistent": 1,
      "inconsistent": 0,
      "not-comparable": 0
     }
    }
   ],
   "rowsOmitted": 0,
   "set": {
    "id": "cmp:df4fc1fb349034375b3236d5",
    "relationLabel": null,
    "conflict": false,
    "rowCount": 2,
    "sourceIds": [
     "tns",
     "tns-public-reports"
    ],
    "definitionCodes": [
     "transient-redshift"
    ],
    "sigmaThreshold": 3.0,
    "maxPairwiseSigma": null,
    "pairCounts": {
     "total": 1,
     "byRelation": {
      "none": 1
     },
     "byVerdict": {
      "consistent": 1,
      "inconsistent": 0,
      "not-comparable": 0
     }
    },
    "notablePairs": [],
    "explanation": "Every assessed pair is consistent under the stated rules.",
    "comparisonPolicyVersion": "ctas-comparison-policy.v1"
   },
   "selections": [
    {
     "id": "cmp:9cce60a6680a3feada5aed3f",
     "ruleId": "ctas-headline",
     "ruleVersion": "v1",
     "rule": "The most recent TNS-reported transient redshift; otherwise a NED host redshift with a stated uncertainty when exactly one host is proposed; otherwise none.",
     "selector": "exporter",
     "reviewState": "unreviewed",
     "propertyCode": "redshift",
     "definitionCode": "transient-redshift",
     "basis": "naming-authority",
     "label": "selection, not a measurement",
     "value": 0.054131,
     "displayValue": "0.054131",
     "unit": "1",
     "errPlus": null,
     "errMinus": null,
     "chosenRow": 0,
     "chosenSourceRecordIds": [
      "tns-hourly:2026zfw"
     ],
     "rejectedRowCount": 1,
     "matchesDisplayedValue": true,
     "reason": "Chosen by ctas-headline.v1 (naming-authority); the source rows are unchanged."
    }
   ]
  }
 ]
};
const candidate = {event_id: "15155e9e-052b-4294-abc4-b48c645ad118", name: "SN2026zfw", comparison: BLOCK};

test("no block, no panel and no brief link", () => {
  assert.equal(comparison.panel({event_id: "x", name: "AT1"}), "");
  assert.equal(comparison.briefLink({event_id: "x"}), "");
  assert.equal(comparison.panel({event_id: "x", comparison: {schema: "other", properties: [{}]}}), "");
});

test("the panel lists every row, the rule, the selection and the conflict, escaped", () => {
  const html = comparison.panel(candidate);
  assert.match(html, /data-dossier-view="comparison"/);
  assert.equal((html.match(/data-comparison-row/g) || []).length, 6);
  assert.match(html, /In conflict: Statistically inconsistent/);
  assert.match(html, /In conflict: Status conflict/);
  assert.match(html, /selection, not a measurement/);
  assert.match(html, /tolerance 2 arcsec/);
  assert.match(html, /2026TNSCR3448\.\.\.\.1S/);
  assert.doesNotMatch(html, /SN II <b>/);
  assert.match(html, /SN II &lt;b&gt;/);
  assert.match(html, /nothing averaged/);
  assert.match(html, /<th scope="col">Compatibility<\/th>/);
  assert.match(html, /aria-label="Select row 1 \(tns\) for export"/);
  assert.match(html, / open>/, "a block with conflicts opens by default");
});

test("the brief link counts the properties in conflict and points at the panel", () => {
  const link = comparison.briefLink(candidate);
  assert.match(link, /2 properties in conflict \(Classification, Position\) — compare sources/);
  assert.match(link, /href="#ctas-comparison-15155e9e"/);
  const calm = JSON.parse(JSON.stringify(candidate));
  calm.comparison.summary.propertiesInConflict = 0;
  assert.match(comparison.briefLink(calm), /no conflict under the stated rules/);
});

test("flat rows keep the source values and the receipts", () => {
  const rows = comparison.flatRows(candidate);
  assert.equal(rows.length, 6);
  const position = rows.filter((row) => row.property === "position");
  assert.deepEqual(position.map((row) => row.source_id).sort(), ["tns", "tns-public-reports"]);
  assert.ok(rows.every((row) => /^[0-9a-f]{64}$/.test(row.receipt_sha256)));
  const one = comparison.flatRows(candidate, [BLOCK.properties[0].rows[0].id]);
  assert.equal(one.length, 1);
});

const parseEcsvLine = (line) => {
  const out = []; let i = 0;
  while (i < line.length) {
    if (line[i] === " ") { i += 1; continue; }
    if (line[i] === '"') { let j = i + 1, s = ""; while (j < line.length) { if (line[j] === '"' && line[j + 1] === '"') { s += '"'; j += 2; } else if (line[j] === '"') break; else s += line[j++]; } out.push(s); i = j + 1; }
    else { let j = line.indexOf(" ", i); if (j < 0) j = line.length; out.push(line.slice(i, j)); i = j; }
  }
  return out;
};

test("ECSV: an astropy header and one well-formed line per row", () => {
  const rows = comparison.flatRows(candidate);
  const text = comparison.toEcsv(candidate, rows);
  const lines = text.trim().split("\n");
  assert.equal(lines[0], "# %ECSV 1.0");
  assert.ok(lines.some((line) => line === "# - {name: err_plus, datatype: float64}"));
  const data = lines.filter((line) => !line.startsWith("#"));
  const header = data[0].split(" ");
  assert.equal(data.length, rows.length + 1);
  for (const line of data.slice(1)) assert.equal(parseEcsvLine(line).length, header.length, line);
  assert.ok(text.includes('"SN II <b>"'));
});

test("VOTable: one TR per row and one TD per FIELD, XML-escaped", () => {
  const rows = comparison.flatRows(candidate);
  const text = comparison.toVotable(candidate, rows);
  const fields = (text.match(/<FIELD /g) || []).length;
  const trs = text.match(/<TR>.*?<\/TR>/g) || [];
  assert.equal(trs.length, rows.length);
  for (const tr of trs) assert.equal((tr.match(/<TD>/g) || []).length, fields);
  assert.ok(text.includes("SN II &lt;b&gt;"));
  assert.ok(text.startsWith('<?xml version="1.0"'));
});

test("citations name the bibcodes and every source reference", () => {
  const cites = comparison.citations(comparison.flatRows(candidate));
  assert.ok(cites.some((row) => row.bibcode === "2026TNSCR3448....1S"));
  assert.ok(cites.some((row) => row.bibcode === "2026TNSTR3377....1T"));
  assert.ok(cites.some((row) => row.software));
});

const pending = [];
test("the manifest's checksums cover exactly the exported files", () => {
  const rows = comparison.flatRows(candidate, BLOCK.properties[1].rows.map((row) => row.id));
  pending.push(comparison.manifest(candidate, rows).then((doc) => {
    const ecsv = comparison.toEcsv(candidate, rows), vot = comparison.toVotable(candidate, rows);
    assert.equal(doc.rows, 2);
    assert.equal(doc.files[0].sha256, crypto.createHash("sha256").update(ecsv).digest("hex"));
    assert.equal(doc.files[1].sha256, crypto.createHash("sha256").update(vot).digest("hex"));
    assert.equal(doc.files[0].name, "ctas-comparison-SN2026zfw.ecsv");
    assert.equal(doc.comparison_policy, "ctas-comparison-policy.v1");
  }));
});

(async () => {
  for (const [name, fn] of tests) { fn(); passed += 1; console.log(`ok  ${name}`); }
  await Promise.all(pending);
  console.log(`${passed} comparison panel tests passed`);
})().catch((error) => { console.error(error); process.exit(1); });
