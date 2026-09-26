(function (root, factory) {
  "use strict";
  var loader = factory();
  if (typeof module === "object" && module.exports) module.exports = loader;
  root.CTASChunkLoader = loader;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  var CHUNK_SCHEMA = "ctas.public-candidate-chunk@1.0.0";
  var DESCRIPTOR_SCHEMA = "ctas.candidate-chunk-parts@1.0.0";
  var PART_SCHEMA = "ctas.candidate-json-part@1.0.0";
  var MAX_FILE_BYTES = 4 * 1024 * 1024;
  var ROOT_PATH = /^ctas\/data\/candidate-chunks\/([0-9a-f]{2,4})\.json$/;
  var PART_PATH = /^ctas\/data\/candidate-chunks\/[0-9a-f]{3}\.part-[0-9]{6}\.json$/;

  function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function integer(value, minimum) { return Number.isSafeInteger(value) && value >= minimum; }
  function sha(value) { return typeof value === "string" && /^[0-9a-f]{64}$/.test(value); }
  function metadataValid(metadata, pattern) {
    return object(metadata) && typeof metadata.path === "string" && pattern.test(metadata.path) &&
      integer(metadata.bytes, 1) && metadata.bytes <= MAX_FILE_BYTES && sha(metadata.sha256);
  }
  function parse(bytes, label) {
    var value;
    try { value = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes)); }
    catch (_) { throw new Error("Invalid " + label + " JSON."); }
    if (!object(value)) throw new Error("Invalid " + label + " document.");
    return value;
  }
  function validateChunk(document_, bucket, count) {
    if (document_.schema !== CHUNK_SCHEMA || document_.bucket !== bucket ||
        !integer(document_.candidate_count, 0) || document_.candidate_count !== count ||
        !Array.isArray(document_.candidates) || document_.candidates.length !== count ||
        !document_.candidates.every(object)) {
      throw new Error("Detail-shard schema, bucket, or candidate count does not match its release manifest.");
    }
    return document_;
  }
  async function verify(bytes, metadata, digest, assertCurrent) {
    assertCurrent();
    if (!(bytes instanceof ArrayBuffer) && !ArrayBuffer.isView(bytes)) throw new Error("Detail-shard response is not bytes.");
    if (bytes.byteLength !== metadata.bytes || bytes.byteLength > MAX_FILE_BYTES) {
      throw new Error("Detail-shard byte length does not match its release manifest or exceeds the download limit.");
    }
    var checksum = await digest(bytes);
    assertCurrent();
    if (checksum !== metadata.sha256) throw new Error("Detail-shard SHA-256 does not match its release manifest; refresh after publication finishes.");
  }

  // The logical root stays at its stable bucket URL. Oversized records are
  // reconstructed only when requested, with every fragment and the completed
  // original JSON independently verified before any candidate is returned.
  async function decode(bytes, metadata, declaredParts, readBytes, digest, assertCurrent) {
    assertCurrent = assertCurrent || function () {};
    assertCurrent();
    if (!metadataValid(metadata, ROOT_PATH) || !integer(metadata.candidate_count, 0)) {
      throw new Error("The release manifest does not bind this detail shard.");
    }
    if (!Array.isArray(declaredParts)) throw new Error("The release manifest has invalid supplemental parts.");
    var declared = new Map();
    declaredParts.forEach(function (part) {
      if (!metadataValid(part, PART_PATH) || declared.has(part.path)) throw new Error("The release manifest has invalid or duplicate supplemental parts.");
      declared.set(part.path, part);
    });
    await verify(bytes, metadata, digest, assertCurrent);
    assertCurrent();
    var document_ = parse(bytes, "candidate chunk"), bucket = metadata.path.match(ROOT_PATH)[1];
    if (document_.schema !== DESCRIPTOR_SCHEMA) return validateChunk(document_, bucket, metadata.candidate_count);
    if (!/^[0-9a-f]{3}$/.test(bucket) || document_.bucket !== bucket ||
        !integer(document_.candidate_count, 0) || document_.candidate_count !== metadata.candidate_count ||
        !integer(document_.assembled_bytes, 1) || !sha(document_.assembled_sha256) ||
        !Array.isArray(document_.parts) || !document_.parts.length || document_.parts.length > 999999) {
      throw new Error("Invalid multipart candidate chunk metadata.");
    }
    var fragments = [], assembledLength = 0;
    for (var i = 0; i < document_.parts.length; i += 1) {
      var ordinal = i + 1;
      var expectedPath = "ctas/data/candidate-chunks/" + bucket + ".part-" + String(ordinal).padStart(6, "0") + ".json";
      var partMetadata = document_.parts[i], binding = declared.get(expectedPath);
      if (!metadataValid(partMetadata, PART_PATH) || partMetadata.path !== expectedPath || !binding ||
          partMetadata.bytes !== binding.bytes || partMetadata.sha256 !== binding.sha256) {
        throw new Error("Candidate chunk parts must be safe, contiguous, ordered, and bound by the release manifest.");
      }
      assertCurrent();
      var partBytes = await readBytes(expectedPath);
      assertCurrent();
      await verify(partBytes, partMetadata, digest, assertCurrent);
      assertCurrent();
      var part = parse(partBytes, "candidate chunk part"), fragment = part.json_fragment;
      if (part.schema !== PART_SCHEMA || part.bucket !== bucket || part.part !== ordinal ||
          typeof fragment !== "string" || !fragment.length || /[^\x00-\x7f]/.test(fragment)) {
        throw new Error("Invalid candidate chunk part content.");
      }
      assembledLength += fragment.length;
      if (assembledLength > document_.assembled_bytes) throw new Error("Assembled candidate chunk exceeds its declared byte length.");
      fragments.push(fragment);
    }
    var assembled = new TextEncoder().encode(fragments.join(""));
    if (assembled.byteLength !== document_.assembled_bytes) throw new Error("Assembled candidate chunk byte length does not match its descriptor.");
    var assembledChecksum = await digest(assembled);
    assertCurrent();
    if (assembledChecksum !== document_.assembled_sha256) throw new Error("Assembled candidate chunk SHA-256 does not match its descriptor.");
    return validateChunk(parse(assembled, "assembled candidate chunk"), bucket, metadata.candidate_count);
  }
  function isGzip(bytes) {
    var view = new Uint8Array(bytes, 0, Math.min(2, bytes.byteLength));
    return view.length === 2 && view[0] === 0x1f && view[1] === 0x8b;
  }
  async function gunzip(bytes) {
    if (typeof DecompressionStream !== "function") {
      throw new Error("This browser cannot decompress the published catalog files (no DecompressionStream).");
    }
    var stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Response(stream).arrayBuffer();
  }
  // The deployed site serves each dossier root and part as <path>.gz (the release commit
  // keeps plain JSON; scripts/compress_ctas_chunks.sh compresses the deployed copy and sets
  // ctas/delivery.json to "gzip"). With encoding "gzip" this fetches <path>.gz and returns
  // the decompressed JSON bytes, which the caller verifies against the manifest's plain-JSON
  // length and SHA-256 exactly as before; a server that already decoded the gzip (a
  // Content-Encoding header) is recognised by the magic number. Any other encoding fetches
  // the plain path. Exactly one request is made per file, so nothing probes for 404s.
  async function fetchPublished(url, fetchImpl, encoding) {
    var get = fetchImpl || fetch;
    var compressed = encoding === "gzip";
    var target = compressed ? url + ".gz" : url;
    var response = await get(target, {cache: "no-cache"});
    if (!response.ok) throw new Error(target + " returned HTTP " + response.status);
    var bytes = await response.arrayBuffer();
    return compressed && isGzip(bytes) ? gunzip(bytes) : bytes;
  }
  function chunkEncoding(descriptor) {
    return descriptor && descriptor.schema === "ctas.delivery@1.0.0" &&
      descriptor.candidate_chunk_encoding === "gzip" ? "gzip" : "identity";
  }
  return Object.freeze({decode: decode, fetchPublished: fetchPublished, chunkEncoding: chunkEncoding, CHUNK_SCHEMA: CHUNK_SCHEMA,
    DESCRIPTOR_SCHEMA: DESCRIPTOR_SCHEMA, PART_SCHEMA: PART_SCHEMA, MAX_FILE_BYTES: MAX_FILE_BYTES});
}));
