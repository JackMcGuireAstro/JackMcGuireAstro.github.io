#!/usr/bin/env node
"use strict";
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const loader = require("../ctas/chunk-loader.js");
const hash = bytes => crypto.createHash("sha256").update(new Uint8Array(bytes.buffer || bytes, bytes.byteOffset || 0, bytes.byteLength)).digest("hex");
const digest = async bytes => hash(bytes);
const raw = value => Buffer.from(JSON.stringify(value).replace(/[\u007f-\uffff]/g, c => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0")) + "\n", "ascii");
const clone = value => JSON.parse(JSON.stringify(value));
const rootPath = bucket => "ctas/data/candidate-chunks/" + bucket + ".json";
const meta = (path, bytes) => ({path, bytes: bytes.byteLength, sha256: hash(bytes)});
const sample = () => ({schema: loader.CHUNK_SCHEMA, bucket: "efe", candidate_count: 1,
  candidates: [{event_id: "event-1", name: "AT2026test", observations: [{value: 1.23, error: null, label: "λ ⭑"}],
    provenance: ["source α", "source β"], query_receipts: ["first", "second"]}]});
function multipart(document_, slice = 64) {
  const complete = raw(document_), artifacts = new Map(), parts = [];
  const text = complete.toString("ascii");
  for (let offset = 0, ordinal = 1; offset < text.length; offset += slice, ordinal += 1) {
    const path = "ctas/data/candidate-chunks/" + document_.bucket + ".part-" + String(ordinal).padStart(6, "0") + ".json";
    const bytes = raw({schema: loader.PART_SCHEMA, bucket: document_.bucket, part: ordinal,
      json_fragment: text.slice(offset, offset + slice)});
    artifacts.set(path, bytes); parts.push(meta(path, bytes));
  }
  const descriptor = {schema: loader.DESCRIPTOR_SCHEMA, bucket: document_.bucket, candidate_count: document_.candidate_count,
    assembled_bytes: complete.byteLength, assembled_sha256: hash(complete), parts};
  return {document_, descriptor, parts: clone(parts), artifacts};
}
function root(fixture) {
  const bytes = raw(fixture.descriptor);
  return {bytes, metadata: {...meta(rootPath(fixture.document_.bucket), bytes), candidate_count: fixture.document_.candidate_count}};
}
function read(fixture, path) {
  if (!fixture.artifacts.has(path)) throw new Error("Missing candidate part");
  return fixture.artifacts.get(path);
}
function decode(fixture, readOverride, hashOverride, guard) {
  const {bytes, metadata} = root(fixture);
  return loader.decode(bytes, metadata, fixture.parts, readOverride || (async path => read(fixture, path)), hashOverride || digest, guard);
}
function replacePart(fixture, index, modify) {
  const path = fixture.descriptor.parts[index].path;
  const part = JSON.parse(fixture.artifacts.get(path)); modify(part);
  const bytes = raw(part); fixture.artifacts.set(path, bytes);
  fixture.descriptor.parts[index] = meta(path, bytes); fixture.parts[index] = meta(path, bytes);
}
let passed = 0;
async function test(name, fn) { await fn(); passed += 1; console.log("PASS " + name); }
async function rejected(name, mutate, pattern = /./) {
  await test(name, async () => { const fixture = multipart(sample()); mutate(fixture); await assert.rejects(decode(fixture), pattern); });
}
async function main() {
  await test("ordinary current and legacy bucket files preserve every field without supplemental reads", async () => {
    for (const bucket of ["fe", "efe", "0efe"]) {
      const document_ = sample(); document_.bucket = bucket;
      const bytes = raw(document_), metadata = {...meta(rootPath(bucket), bytes), candidate_count: 1};
      const result = await loader.decode(bytes, metadata, [], () => { throw new Error("Unexpected supplemental read"); }, digest);
      assert.deepEqual(result, document_);
    }
  });
  await test("multipart Unicode, array ordering and observation precision are exact", async () => {
    const fixture = multipart(sample()); assert.deepEqual(await decode(fixture), fixture.document_);
  });
  await test("a single record above 5.6 MB is complete and every download stays within 4 MiB", async () => {
    const document_ = sample();
    document_.candidates[0].query_receipts = Array.from({length: 5600}, (_, n) => ({ordinal: n,
      payload: "retained provenance ".repeat(54), url: "https://example.org/receipt/" + n, uncertainties: [null, 0.123456789]}));
    assert(raw(document_).byteLength > 5600000);
    const fixture = multipart(document_, 1024 * 1024), {bytes} = root(fixture);
    assert(bytes.byteLength <= loader.MAX_FILE_BYTES);
    for (const part of fixture.artifacts.values()) assert(part.byteLength <= loader.MAX_FILE_BYTES);
    const result = await decode(fixture);
    assert.deepEqual(result, document_);
    assert.equal(result.candidates[0].query_receipts.length, 5600);
  });
  await test("root byte corruption is rejected before any data reads", async () => {
    const fixture = multipart(sample()), {bytes, metadata} = root(fixture); bytes[10] ^= 1;
    await assert.rejects(loader.decode(bytes, metadata, fixture.parts, () => { throw new Error("Unexpected read"); }, digest), /SHA-256/);
  });
  await rejected("missing supplemental file is rejected", f => f.artifacts.delete(f.parts[0].path), /Missing/);
  await rejected("same-length corrupt supplemental bytes are rejected", f => { const bytes = Buffer.from(f.artifacts.get(f.parts[0].path)); bytes[10] ^= 1; f.artifacts.set(f.parts[0].path, bytes); }, /SHA-256/);
  await rejected("truncated supplemental bytes are rejected", f => f.artifacts.set(f.parts[0].path, f.artifacts.get(f.parts[0].path).subarray(1)), /byte length/);
  await rejected("reordered parts are rejected", f => f.descriptor.parts.reverse(), /contiguous/);
  await rejected("duplicate descriptor parts are rejected", f => { f.descriptor.parts[1] = f.descriptor.parts[0]; }, /contiguous/);
  await rejected("undeclared part is rejected", f => f.parts.shift(), /bound/);
  await rejected("duplicate global part declaration is rejected", f => f.parts.push(clone(f.parts[0])), /duplicate/);
  await rejected("manifest and descriptor metadata disagreement is rejected", f => f.parts[0].bytes += 1, /bound/);
  await rejected("cross-bucket part references are rejected", f => { f.descriptor.parts[0].path = f.descriptor.parts[0].path.replace("efe", "123"); }, /contiguous/);
  await rejected("unsafe global path is rejected", f => { f.parts[0].path = "ctas/data/../private.json"; }, /invalid/);
  await rejected("noncontiguous part ordinals are rejected", f => replacePart(f, 0, p => { p.part = 2; }), /content/);
  await rejected("wrong part schema is rejected", f => replacePart(f, 0, p => { p.schema = loader.CHUNK_SCHEMA; }), /content/);
  await rejected("wrong part bucket is rejected", f => replacePart(f, 0, p => { p.bucket = "abc"; }), /content/);
  await rejected("unescaped non-ASCII fragments are rejected", f => replacePart(f, 0, p => { p.json_fragment += "λ"; }), /content/);
  await rejected("assembled hash mismatch is rejected", f => { f.descriptor.assembled_sha256 = "0".repeat(64); }, /Assembled.*SHA-256/);
  await rejected("assembled declared length overflow is rejected", f => { f.descriptor.assembled_bytes -= 1; }, /exceeds/);
  await rejected("assembled truncation is rejected", f => { f.descriptor.parts.pop(); }, /byte length/);
  await rejected("assembled candidate count mismatch is rejected", f => { f.descriptor.candidate_count = 2; }, /metadata/);
  await test("assembled document identity is checked after reconstruction", async () => {
    const document_ = sample(); document_.candidate_count = 2;
    const fixture = multipart(document_); await assert.rejects(decode(fixture), /candidate count/);
  });
  await test("a release change during a part read rejects the entire candidate without further reads", async () => {
    const fixture = multipart(sample()); let current = true, reads = 0;
    await assert.rejects(decode(fixture, async path => { reads += 1; current = false; return read(fixture, path); }, null,
      () => { if (!current) throw new Error("Release changed"); }), /Release changed/);
    assert.equal(reads, 1);
  });
  await test("a release change during verification rejects both ordinary and assembled results", async () => {
    const fixture = multipart(sample()); let current = true, hashes = 0;
    await assert.rejects(decode(fixture, null, async bytes => {
      hashes += 1; if (hashes === fixture.parts.length + 2) current = false; return hash(bytes);
    }, () => { if (!current) throw new Error("Release changed"); }), /Release changed/);
    const document_ = sample(), bytes = raw(document_), metadata = {...meta(rootPath(document_.bucket), bytes), candidate_count: 1};
    current = true;
    await assert.rejects(loader.decode(bytes, metadata, [], () => {}, async data => { current = false; return hash(data); },
      () => { if (!current) throw new Error("Release changed"); }), /Release changed/);
  });
  await test("individual download byte budget cannot be raised by metadata", async () => {
    const document_ = sample(); document_.candidates[0].payload = "x".repeat(loader.MAX_FILE_BYTES);
    const bytes = raw(document_), metadata = {...meta(rootPath(document_.bucket), bytes), candidate_count: 1};
    await assert.rejects(loader.decode(bytes, metadata, [], () => {}, digest), /does not bind/);
  });
  console.log(passed + " CTAS chunk-loader tests passed.");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
