/* Deterministic browser-side AstroEvidence projection and export serializers. */
(function (root, factory) {
  "use strict";
  var api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.CTASAstroEvidence = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  var PROJECTION_VERSION = "ctas-static-astro-evidence@1.0.0";
  var IDENTITY_POLICY = "ctas-provider-scoped-alias@1.0.0";
  var RECONCILIATION_POLICY = "ctas-assertion-first@1.0.0";
  var REDACTED = "[REDACTED]";
  var SENSITIVE_KEY_TOKENS = new Set(["accesstoken", "apikey", "authentication", "authorization", "clientid",
    "clientsecret", "cookie", "credential", "password", "passwd", "resulturl", "secret", "signature",
    "signedurl", "taskurl", "token"]);
  var SENSITIVE_LITERAL = /(?:\bbearer\s+\S+|\b(?:access[_-]?token|api[_-]?key|authorization|client[_-]?secret|password|passwd|secret|signature|token)\s*[=:]\s*[^\s&,;]+)/i;
  var PHYSICAL_TRANSIENT_LABELS = new Set(["active galactic nucleus", "agn", "cataclysmic variable", "cv",
    "kilonova", "luminous red nova", "nova", "tde", "tidal disruption", "tidal disruption event"]);
  var SUPERNOVA_CLASSIFICATION = /^(?:(?:sn|type)\s+(?:i(?:a|ax|b|bc|bn|c|cn)?|ii(?:b|n|p|l)?)|slsn(?:[-\s](?:i|ii))?|sn[-\s]like|supernova)(?:[-\s].*)?$/i;
  var NON_CLASSIFICATION_MARKER = /(?:^|[\s_-])(?:classifier|probability|prob|score|versus|vs)(?:$|[\s_-])/i;

  function text(value) { return value === null || value === undefined ? "" : String(value); }
  function number(value) {
    if (value === null || value === undefined || value === "") return null;
    var parsed = Number(value); return Number.isFinite(parsed) ? parsed : null;
  }
  function sortObject(value) {
    if (Array.isArray(value)) return value.map(sortObject);
    if (!value || typeof value !== "object") return value;
    return Object.keys(value).sort().reduce(function (result, key) {
      result[key] = sortObject(value[key]); return result;
    }, {});
  }
  function canonicalJson(value) { return JSON.stringify(sortObject(value)) + "\n"; }
  function utf8(value) { return new TextEncoder().encode(value); }
  function hex(bytes) {
    return Array.prototype.map.call(new Uint8Array(bytes), function (byte) { return byte.toString(16).padStart(2, "0"); }).join("");
  }
  async function sha256(value) {
    var subtle = root.crypto && root.crypto.subtle;
    if (!subtle && typeof require === "function") subtle = require("crypto").webcrypto.subtle;
    if (!subtle) throw new Error("SHA-256 is unavailable in this browser.");
    return hex(await subtle.digest("SHA-256", typeof value === "string" ? utf8(value) : value));
  }
  async function stableId(kind) {
    var parts = Array.prototype.slice.call(arguments, 1);
    return "ae:" + kind + ":" + (await sha256(parts.map(text).join("\x1f"))).slice(0, 24);
  }
  function iso(value, fallback) {
    var rendered = text(value || fallback).trim();
    if (!rendered) return null;
    if (/^\d{4}-\d{2}-\d{2}$/.test(rendered)) rendered += "T00:00:00Z";
    else if (!/Z$|[+-]\d\d:\d\d$/.test(rendered)) rendered = rendered.replace(" ", "T") + "Z";
    var parsed = new Date(rendered); return Number.isNaN(parsed.getTime()) ? (fallback || null) : parsed.toISOString().replace(".000Z", "Z");
  }
  function validHash(value) { return /^[0-9a-f]{64}$/i.test(text(value)) ? text(value).toLowerCase() : null; }
  function isSensitiveKey(value) {
    var normalized = text(value).toLowerCase().replace(/[^a-z0-9]/g, "");
    return Array.from(SENSITIVE_KEY_TOKENS).some(function (token) { return normalized.indexOf(token) >= 0; });
  }
  function sanitizePublicText(value) {
    if (value === null || value === undefined) return null;
    var rendered = text(value).trim();
    if (!rendered) return rendered;
    if (rendered.indexOf("/Users/") >= 0 || rendered.indexOf(".codex") >= 0) return REDACTED;
    var parsed;
    try { parsed = new URL(rendered); } catch (_) { return SENSITIVE_LITERAL.test(rendered) ? REDACTED : rendered; }
    if (!parsed.protocol || !parsed.host) return SENSITIVE_LITERAL.test(rendered) ? REDACTED : rendered;
    var changed = false;
    if (parsed.username || parsed.password) { parsed.username = ""; parsed.password = ""; changed = true; }
    var query = new URLSearchParams();
    parsed.searchParams.forEach(function (item, key) {
      if (isSensitiveKey(key) || SENSITIVE_LITERAL.test(item)) { query.append(key, REDACTED); changed = true; }
      else query.append(key, item);
    });
    if (changed || parsed.search) parsed.search = query.toString() ? "?" + query.toString() : "";
    var fragment = parsed.hash ? parsed.hash.slice(1) : "";
    if (SENSITIVE_LITERAL.test(fragment)) { parsed.hash = REDACTED; changed = true; }
    return changed ? parsed.toString() : rendered;
  }
  function sanitizePublicValue(value) {
    var redacted = [];
    function scrub(item, path) {
      if (Array.isArray(item)) return item.map(function (child, index) { return scrub(child, path + "[" + index + "]"); });
      if (item && typeof item === "object") {
        var result = {}, existing = item.redactedFields;
        Object.keys(item).sort().forEach(function (key) {
          var childPath = path ? path + "." + key : key;
          if (isSensitiveKey(key)) { result[key] = REDACTED; redacted.push(childPath); }
          else result[key] = scrub(item[key], childPath);
        });
        if (!path && Array.isArray(existing)) existing.forEach(function (row) { if (row) redacted.push(text(row)); });
        return result;
      }
      if (typeof item === "string") {
        var sanitized = sanitizePublicText(item);
        if (sanitized !== item) redacted.push(path);
        return sanitized;
      }
      return item;
    }
    var result = scrub(value, "");
    if (result && typeof result === "object" && !Array.isArray(result) && redacted.length) {
      result.redactedFields = Array.from(new Set(redacted)).sort();
    }
    return result;
  }
  function publicUrl(value) {
    var rendered = sanitizePublicText(value);
    if (!rendered || rendered === REDACTED) return null;
    try { var parsed = new URL(rendered); return parsed.protocol === "https:" && parsed.host ? rendered : null; }
    catch (_) { return null; }
  }
  function publicArtifactReference(value) {
    var rendered = text(value).trim();
    if (/^sha256:[0-9a-f]{64}$/i.test(rendered)) return rendered.toLowerCase();
    var digest = validHash(rendered); return digest ? "sha256:" + digest : publicUrl(rendered);
  }
  function jsonValue(value, fallback) {
    if (value && typeof value === "object") return value;
    if (!value) return fallback;
    try { var parsed = JSON.parse(value); return parsed && typeof parsed === typeof fallback ? parsed : fallback; }
    catch (_) { return fallback; }
  }
  function flags(value, accessed) {
    value = jsonValue(value, Array.isArray(value) ? value : []);
    if (!Array.isArray(value)) value = value ? [value] : [];
    var rows = value.map(function (item) {
      if (item && typeof item === "object") return Object.keys(item).sort().map(function (key) { return key + "=" + item[key]; }).join("; ");
      return text(item);
    }).filter(Boolean);
    if (accessed) rows.push("ctas-accessed-at=" + accessed);
    return Array.from(new Set(rows));
  }
  function firstPresent(row, keys) {
    for (var index = 0; index < keys.length; index += 1) {
      if (row[keys[index]] !== null && row[keys[index]] !== undefined) return row[keys[index]];
    }
    return null;
  }
  function time(row, key, mjdKey, jdKey) {
    mjdKey = mjdKey === undefined ? "mjd" : mjdKey;
    jdKey = jdKey === undefined ? "jd" : jdKey;
    var originalTime = firstPresent(row, ["original_time", "source_original_time"]);
    var mjd = mjdKey ? number(row[mjdKey]) : null, jd = jdKey ? number(row[jdKey]) : null;
    var primary = row[key] === null || row[key] === undefined ? null : row[key];
    var explicitFormat = firstPresent(row, ["time_format", "original_time_format"]);
    var originalValue = null, format = explicitFormat === null ? null : text(explicitFormat);
    if (originalTime !== null) { originalValue = originalTime; format = format || "SOURCE_NATIVE"; }
    else if (jd !== null) { originalValue = jd; format = "JD"; }
    else if (mjd !== null) { originalValue = mjd; format = "MJD"; }
    else if (primary !== null) { originalValue = primary; format = format || "ISO 8601"; }
    var uncertainty = number(firstPresent(row, ["time_uncertainty", "time_uncertainty_seconds"]));
    var scale = firstPresent(row, ["time_scale", "timescale", "original_time_scale"]);
    var reference = firstPresent(row, ["time_reference_position", "reference_position", "time_refposition"]);
    return {originalValue: originalValue, format: format, scale: scale === null ? null : text(scale),
      referencePosition: reference === null ? null : text(reference),
      uncertainty: uncertainty === null ? null : Math.abs(uncertainty),
      normalizedMjd: mjd !== null ? mjd : jd !== null ? jd - 2400000.5 : null};
  }
  function citation(row, sourceId) {
    var url = ["citation_url", "source_url", "canonical_url", "public_download_url"].map(function (key) {
      return publicUrl(row[key]);
    }).find(Boolean) || null;
    return {label: sourceId, url: url, bibcode: null, doi: null, table: null,
      row: text(row.source_record_id || row.provider_observation_id) || null};
  }
  function measurement(options) {
    return {
      measurementId: options.measurementId, targetId: options.targetId,
      sourceContractId: options.sourceId, sourceRecordId: options.sourceRecordId,
      solutionId: null, propertyCode: options.propertyCode, ucd: options.ucd,
      label: options.label, valueKind: options.valueKind, originalValue: options.value,
      originalUnit: options.unit, normalizedValue: options.value, normalizedUnit: options.unit,
      uncertaintyPositive: options.uncertainty, uncertaintyNegative: options.uncertainty,
      intervalLower: null, intervalUpper: null, posteriorProductId: null,
      covarianceState: options.uncertainty !== null ? "NOT_PROVIDED" : "UNKNOWN", covarianceGroupId: null,
      time: options.time, referenceFrame: options.referenceFrame || null, method: options.method || null,
      facility: options.facility || null, instrument: options.instrument || null,
      bandpass: options.bandpass || null, calibration: options.calibration || null,
      qualityFlags: options.flags || [], sourceStatus: options.sourceStatus || null,
      citation: options.citation, transformationRecipe: "Identity projection from the source-native CTAS field; no scientific conversion or averaging.",
      parserVersion: PROJECTION_VERSION, activeState: options.activeState || "ACTIVE"
    };
  }

  function accessMode(row) {
    var raw = [row.access_mode, row.protocol, row.query_scope].map(text).join(" ").toLowerCase();
    if (/stream|live/.test(raw)) return "STREAM";
    if (/bulk|snapshot|delta/.test(raw)) return "BULK_SNAPSHOT";
    if (/manual/.test(raw)) return "MANUAL";
    if (/link|represented|broker-mediated/.test(raw)) return "LINK_ONLY";
    if (/hybrid/.test(raw)) return "HYBRID";
    return "PER_TARGET_QUERY";
  }
  function implementationState(row) {
    var raw = text(row.implementation_state || row.connector_implementation_state).toLowerCase();
    if (/represented/.test(raw)) return "REPRESENTED_THROUGH_PROVIDER";
    if (/credential/.test(raw)) return "CREDENTIAL_REQUIRED";
    if (/authorization|user-author|topic-author/.test(raw)) return "AUTHORIZATION_REQUIRED";
    if (/blocked-policy/.test(raw)) return "BLOCKED_POLICY";
    if (/not-implemented|review-required/.test(raw)) return "NOT_IMPLEMENTED";
    if (/manual|archival|link-only/.test(raw)) return "LINK_ONLY";
    if (raw === "implemented" || /^implemented-/.test(raw)) return "QUERYABLE";
    return "NOT_IMPLEMENTED";
  }
  function rightsState(row) {
    var raw = text(row.rights_or_public_access_basis).toLowerCase();
    var auth = text(row.authentication_requirement).toLowerCase();
    if (/blocked/.test(raw)) return "BLOCKED";
    var publicBasis = /public|open/.test(raw);
    if (!publicBasis && /authorized|user-owned|private/.test(raw)) return "AUTHORIZED_PRIVATE";
    if (publicBasis && /token|key|oauth|registered|account/.test(auth)) return "CREDENTIALLED_PUBLIC";
    if (publicBasis && /attribution|acknowledg|credit|citation/.test(raw)) return "PUBLIC_WITH_ATTRIBUTION";
    if (publicBasis) return "PUBLIC";
    return "UNRESOLVED";
  }
  function classificationProperty(value) {
    var normalized = text(value).trim().toLowerCase().replace(/\s+/g, " ");
    if (["", "n/a", "na", "none", "unknown", "unclassified"].indexOf(normalized) >= 0) return "transient.classification.status";
    if (["below horizon", "bogus", "distant particles", "high-importance", "retracted", "unreliable location"].indexOf(normalized) >= 0) return "alert.operational_label";
    if (NON_CLASSIFICATION_MARKER.test(normalized)) return "alert.event_label";
    var base = normalized.endsWith(" candidate") ? normalized.slice(0, -10) : normalized;
    if (PHYSICAL_TRANSIENT_LABELS.has(base) || SUPERNOVA_CLASSIFICATION.test(normalized)) return "transient.classification";
    return "alert.event_label";
  }
  function messengerRevision(row) {
    var signalId = text(row.provider_signal_id || row.source_record_id);
    var properties = jsonValue(row.properties, {});
    var rawRevision = firstPresent(row, ["messenger_revision", "revision"]);
    if (rawRevision === null && properties && typeof properties === "object") rawRevision = properties.revision;
    var parsed = number(rawRevision), revision = parsed !== null && parsed >= 0 && Number.isInteger(parsed) ? parsed : null;
    var match = signalId.match(/^(.*?):r(?:ev(?:ision)?)?(\d+)(?::(?:initial|update|retraction))?$/i);
    var group = match ? match[1] : signalId;
    if (match && revision === null) revision = Number(match[2]);
    return {group: text(row.messenger_revision_group_id || group || row.assertion_id || "messenger"), revision: revision};
  }
  function messengerRetracted(row) {
    if (row.retracted !== null && row.retracted !== undefined) return Boolean(row.retracted);
    if (/retract/i.test(text(row.role)) || /retract/i.test(text(row.alert_type))) return true;
    var properties = jsonValue(row.properties, {}), comments = properties && properties.comments || [];
    if (!Array.isArray(comments)) comments = [comments];
    return comments.some(function (comment) { return /retract/i.test(text(comment)); });
  }
  function deriveMessengerRevisions(inputRows) {
    var rows = inputRows.map(function (source) {
      var row = Object.assign({}, source), revision = messengerRevision(row);
      row.messenger_revision_group_id = revision.group;
      if (revision.revision !== null) row.messenger_revision = revision.revision;
      row.retracted = messengerRetracted(row);
      return row;
    });
    var groups = {};
    rows.forEach(function (row) {
      var key = text(row.provider).toLowerCase() + "\x1f" + row.messenger_revision_group_id;
      (groups[key] = groups[key] || []).push(row);
    });
    Object.keys(groups).forEach(function (key) {
      var ordered = groups[key].slice().sort(function (left, right) {
        var leftRevision = messengerRevision(left).revision, rightRevision = messengerRevision(right).revision;
        leftRevision = leftRevision === null ? -1 : leftRevision;
        rightRevision = rightRevision === null ? -1 : rightRevision;
        if (leftRevision !== rightRevision) return leftRevision - rightRevision;
        var leftTime = text(left.source_published_at || left.ctas_received_at || left.observed_at);
        var rightTime = text(right.source_published_at || right.ctas_received_at || right.observed_at);
        if (leftTime !== rightTime) return leftTime.localeCompare(rightTime);
        return text(left.provider_signal_id || left.assertion_id).localeCompare(text(right.provider_signal_id || right.assertion_id));
      });
      ordered.forEach(function (row, index) {
        if (index) row.supersedes_provider_signal_id = text(ordered[index - 1].provider_signal_id || ordered[index - 1].assertion_id) || null;
        row.superseded = index < ordered.length - 1;
      });
    });
    return rows;
  }
  function sourceContract(row, matrix, generatedAt) {
    var id = text(row.source_key), types = row.data_types || row.product_contracts || [];
    var limitations = Array.isArray(row.known_limitations) ? row.known_limitations.map(text).filter(Boolean) :
      [text(row.known_limitations || "No additional limitation recorded.")];
    return {
      sourceContractId: id, sourceName: text(row.name || id), authority: text(row.organization_or_facility || row.name || id),
      documentationUrl: publicUrl(row.documentation_url) || "https://jackmcguireastro.github.io/ctas.html#active-sources",
      scientificRole: text(row.source_family || row.primary_family || "declared public evidence source"),
      productTypes: types.length ? types.map(text) : ["provider-defined public metadata"], accessMode: accessMode(row),
      implementationState: implementationState(row), rightsState: rightsState(row),
      requiredAttribution: text(row.rights_or_public_access_basis || "Provider terms and attribution apply."),
      queryScope: text(row.query_scope || row.access_mode || "Declared source-contract scope"),
      applicabilityRule: "ctas-source-applicability@1.0.0: " + text(matrix && matrix.applicabilityRule || "Referenced by this event's declared applicability set."),
      freshnessSloSeconds: null,
      rateLimitPolicy: text(row.rate_or_cadence_limit || "Provider documentation does not state a numeric limit."),
      authenticationRequirement: text(row.authentication_requirement || "Not documented"),
      parserVersion: PROJECTION_VERSION, schemaVersion: text(row.contract_version || "1.0.0"),
      identityPolicyVersion: IDENTITY_POLICY, reconciliationPolicyVersion: RECONCILIATION_POLICY,
      knownLimitations: limitations,
      lastVerifiedAt: iso(row.last_verified, generatedAt) || generatedAt
    };
  }

  async function buildMeasurements(candidate) {
    var targetId = candidate.event_id, follow = candidate.follow_up || {}, rows = [];
    var classes = (follow.classifications || []).concat(follow.classification_history || []);
    for (var classIndex = 0; classIndex < classes.length; classIndex += 1) {
      var classification = classes[classIndex], sourceId = text(classification.provider || "unknown-source").toLowerCase();
      var assertionId = text(classification.assertion_id) || await stableId("source-record", targetId, sourceId, classification.asserted_at, classification.classification);
      var classificationValue = [classification.classification, classification.subtype].map(text).filter(Boolean).join(" ") || "Unclassified";
      var classificationCode = classificationProperty(classificationValue);
      var active = classification.retracted ? "RETRACTED" : classification.superseded ? "SUPERSEDED" : "ACTIVE";
      var base = {targetId: targetId, sourceId: sourceId, sourceRecordId: text(classification.source_record_id || assertionId),
        time: time(classification, "asserted_at"), referenceFrame: null, facility: null, instrument: null,
        bandpass: null, calibration: null, flags: flags(classification.quality_flags, iso(classification.ctas_received_at)),
        citation: citation(classification, sourceId), activeState: active};
      rows.push(measurement(Object.assign({}, base, {measurementId: await stableId("measurement", "classification", assertionId),
        propertyCode: classificationCode, ucd: null, label: "Source-reported classification or alert label",
        valueKind: "QUALITATIVE", value: classificationValue, unit: null,
        uncertainty: null, method: classification.method, sourceStatus: "source assertion"})));
      var probability = number(classification.probability);
      if (probability !== null) rows.push(measurement(Object.assign({}, base, {
        measurementId: await stableId("measurement", "classification-probability", assertionId),
        propertyCode: classificationCode === "transient.classification" ? "transient.classification.probability" : classificationCode + ".probability",
        ucd: "stat.probability", label: "Source-reported classification probability",
        valueKind: "MEASUREMENT", value: probability, unit: "1", uncertainty: null,
        method: [classification.method, classification.model_name, classification.model_version].filter(Boolean).join(" · ") || null,
        sourceStatus: "source probability; calibration not assumed"})));
    }
    var observations = follow.observations || [];
    for (var observationIndex = 0; observationIndex < observations.length; observationIndex += 1) {
      var observation = observations[observationIndex], provider = text(observation.provider || "unknown-source").toLowerCase();
      var observationId = text(observation.assertion_id) || await stableId("source-record", targetId, provider, observation.observed_at, observation.provider_observation_id);
      var common = {targetId: targetId, sourceId: provider,
        sourceRecordId: text(observation.provider_observation_id || observation.source_record_id || observationId),
        time: time(observation, "observed_at"), referenceFrame: null,
        method: observation.photometry_method || observation.pipeline || null,
        facility: observation.observatory || observation.telescope || null, instrument: observation.instrument || null,
        bandpass: observation.band || observation.original_band || null, calibration: observation.calibration || null,
        flags: flags(observation.quality_flags, iso(observation.ctas_received_at)),
        sourceStatus: observation.detection ? "detection" : "nondetection or limit", citation: citation(observation, provider),
        activeState: observation.superseded ? "SUPERSEDED" : "ACTIVE"};
      var magnitude = number(observation.magnitude), magnitudeError = number(observation.magnitude_error);
      if (magnitude !== null) rows.push(measurement(Object.assign({}, common, {
        measurementId: await stableId("measurement", "magnitude", observationId), propertyCode: "phot.mag",
        ucd: "phot.mag", label: "Source-native magnitude", valueKind: "MEASUREMENT", value: magnitude,
        unit: "mag", referenceFrame: observation.magnitude_system || null,
        uncertainty: magnitudeError === null ? null : Math.abs(magnitudeError)})));
      var flux = number(observation.flux), fluxError = number(observation.flux_error);
      if (flux !== null) rows.push(measurement(Object.assign({}, common, {
        measurementId: await stableId("measurement", "flux", observationId), propertyCode: "phot.flux",
        ucd: "phot.flux.density", label: "Source-native flux", valueKind: "MEASUREMENT", value: flux,
        unit: observation.flux_unit || null, uncertainty: fluxError === null ? null : Math.abs(fluxError)})));
      var limitingMagnitude = number(observation.limiting_magnitude);
      if (limitingMagnitude !== null) rows.push(measurement(Object.assign({}, common, {
        measurementId: await stableId("measurement", "limiting-magnitude", observationId), propertyCode: "phot.mag",
        ucd: "phot.mag", label: "Source-native limiting magnitude (object is fainter)", valueKind: "LOWER_LIMIT",
        value: limitingMagnitude, unit: "mag", referenceFrame: observation.magnitude_system || null, uncertainty: null})));
      var limitingFlux = number(observation.limiting_flux);
      if (limitingFlux !== null) rows.push(measurement(Object.assign({}, common, {
        measurementId: await stableId("measurement", "limiting-flux", observationId), propertyCode: "phot.flux",
        ucd: "phot.flux.density", label: "Source-native flux upper limit", valueKind: "UPPER_LIMIT",
        value: limitingFlux, unit: observation.flux_unit || null, uncertainty: null})));
      var detectionState = observation.detection === true ? "DETECTION" : observation.detection === false ? "NONDETECTION" : "UNSPECIFIED";
      rows.push(measurement(Object.assign({}, common, {
        measurementId: await stableId("measurement", "detection-state", observationId),
        propertyCode: "photometry.detection_state", ucd: "meta.code",
        label: "Source-reported photometric detection state", valueKind: "QUALITATIVE",
        value: detectionState, unit: null, uncertainty: null})));
    }
    var messengerSignals = deriveMessengerRevisions(follow.messenger_signals || []);
    for (var messengerIndex = 0; messengerIndex < messengerSignals.length; messengerIndex += 1) {
      var signal = messengerSignals[messengerIndex], messengerSource = text(signal.provider || "unknown-source").toLowerCase();
      var signalId = text(signal.assertion_id) || await stableId(
        "source-record", targetId, messengerSource, signal.provider_signal_id, signal.observed_at
      );
      var signalRecordId = text(signal.provider_signal_id || signal.source_record_id || signalId);
      var revision = messengerRevision(signal), signalFlags = flags(signal.quality_flags);
      if (revision.revision !== null) signalFlags.push("messenger-revision=" + revision.revision);
      if (revision.group) signalFlags.push("messenger-revision-group=" + revision.group);
      if (signal.supersedes_provider_signal_id) signalFlags.push("supersedes-provider-signal-id=" + signal.supersedes_provider_signal_id);
      var signalAccessed = iso(signal.ctas_received_at); if (signalAccessed) signalFlags.push("ctas-accessed-at=" + signalAccessed);
      rows.push(measurement({targetId: targetId, sourceId: messengerSource, sourceRecordId: signalRecordId,
        measurementId: await stableId("measurement", "messenger-notice", signalId), propertyCode: "messenger.notice.role",
        ucd: "meta.code", label: "Source-reported messenger notice role", valueKind: "QUALITATIVE",
        value: text(signal.role || signal.alert_type || "notice"), unit: null, uncertainty: null,
        time: time(signal, "observed_at"), referenceFrame: null, method: signal.messenger || null,
        facility: null, instrument: signal.instrument || null, bandpass: null, calibration: null, flags: signalFlags,
        sourceStatus: text(signal.alert_type || signal.summary || "source messenger notice"),
        citation: citation(signal, messengerSource),
        activeState: signal.retracted ? "RETRACTED" : signal.superseded ? "SUPERSEDED" : "ACTIVE"}));
    }
    var hosts = follow.host_context || [];
    for (var hostIndex = 0; hostIndex < hosts.length; hostIndex += 1) {
      var host = hosts[hostIndex], redshift = number(host.redshift); if (redshift === null) continue;
      var hostSource = text(host.provider || "unknown-source").toLowerCase();
      var hostId = text(host.assertion_id) || await stableId("source-record", targetId, hostSource, host.queried_at, host.canonical_name);
      var redshiftError = number(host.redshift_error);
      rows.push(measurement({targetId: targetId, sourceId: hostSource, sourceRecordId: text(host.source_record_id || hostId),
        measurementId: await stableId("measurement", "host-redshift", hostId), propertyCode: "src.redshift;meta.id.assoc", ucd: "src.redshift",
        label: "Source-reported host redshift", valueKind: "MEASUREMENT", value: redshift, unit: "1",
        uncertainty: redshiftError === null ? null : Math.abs(redshiftError), time: time(host, "queried_at"),
        referenceFrame: host.redshift_reference || null, method: "provider host-context query", facility: null,
        instrument: null, bandpass: null, calibration: null, flags: flags(host.quality_flags, iso(host.ctas_received_at || host.queried_at)),
        sourceStatus: "reported host association; not independently validated by CTAS", citation: citation(host, hostSource), activeState: "ACTIVE"}));
    }
    return rows.sort(function (a, b) { return a.measurementId.localeCompare(b.measurementId); });
  }

  function sanitizeReceipt(row) {
    return {
      receiptId: text(row.receiptId), targetId: text(row.targetId), sourceContractId: text(row.sourceContractId),
      queryKind: row.queryKind, applicabilityState: row.applicabilityState, outcome: row.outcome,
      scope: sanitizePublicText(row.scope), startedAt: row.startedAt, completedAt: row.completedAt,
      requestFingerprintSha256: row.requestFingerprintSha256, responseChecksumSha256: row.responseChecksumSha256,
      recordsSeen: row.recordsSeen, recordsRetained: row.recordsRetained, recordsRejected: row.recordsRejected,
      paginationComplete: row.paginationComplete, parserVersion: row.parserVersion, schemaVersion: row.schemaVersion,
      isCurrent: row.isCurrent, evidenceUrl: publicUrl(row.evidenceUrl), errorCode: sanitizePublicText(row.errorCode),
      errorDetail: sanitizePublicText(row.errorDetail), nextEligibleAt: row.nextEligibleAt,
      staleReceiptId: row.staleReceiptId
    };
  }
  function sanitizeReceiptDetail(row) {
    return {
      receiptId: text(row.receiptId), sourceContractId: text(row.sourceContractId),
      targetIdentity: sanitizePublicValue(row.targetIdentity), providerRelease: sanitizePublicText(row.providerRelease),
      normalizedRequest: sanitizePublicValue(row.normalizedRequest), responseStatus: sanitizePublicText(row.responseStatus),
      pagination: sanitizePublicValue(row.pagination), caps: sanitizePublicValue(row.caps),
      immutableArtifactReference: publicArtifactReference(row.immutableArtifactReference),
      latencyMs: row.latencyMs, retryCount: row.retryCount, errorCategory: sanitizePublicText(row.errorCategory),
      metadataCompleteness: text(row.metadataCompleteness || "LEGACY_NULLABLE"), executionState: row.executionState
    };
  }
  function recordCap(detail) {
    var caps = detail.caps;
    if (!caps || typeof caps !== "object" || Array.isArray(caps)) return null;
    return firstPresent(caps, ["recordCap", "record_cap"]);
  }
  function missingValue(value) { return value === null || value === undefined; }
  function receiptCompleteness(receipt, detail) {
    var required = {executionState: detail.executionState, targetIdentity: detail.targetIdentity,
      startedAt: receipt.startedAt, completedAt: receipt.completedAt};
    var executionOutcomes = ["DATA_RETURNED", "PARTIAL_RESULT", "SEARCHED_NO_MATCH", "QUERY_FAILED"];
    var outcome = receipt.outcome, state = detail.executionState;
    var requiresExecution = state === "EXECUTED" || (missingValue(state) && executionOutcomes.indexOf(outcome) >= 0);
    if (state === "NOT_EXECUTED" && executionOutcomes.indexOf(outcome) >= 0) required.requestExecutionOutcomeConsistency = null;
    if (state === "EXECUTED" && ["LINK_ONLY_NOT_QUERIED", "NOT_APPLICABLE", "NOT_CONFIGURED", "NOT_QUERIED"].indexOf(outcome) >= 0) {
      required.requestExecutionOutcomeConsistency = null;
    }
    if (requiresExecution) {
      required.providerRelease = detail.providerRelease;
      required.normalizedRequest = detail.normalizedRequest;
      required.requestFingerprintSha256 = validHash(receipt.requestFingerprintSha256);
      required.responseStatus = detail.responseStatus;
      required.paginationOrRecordCap = detail.pagination !== null && detail.pagination !== undefined ? detail.pagination : recordCap(detail);
      required.recordsSeen = receipt.recordsSeen; required.recordsRetained = receipt.recordsRetained;
      required.recordsRejected = receipt.recordsRejected;
      required.parserVersion = receipt.parserVersion === "legacy-receipt:not-recorded" ? null : receipt.parserVersion;
      required.schemaVersion = receipt.schemaVersion === "legacy-receipt:not-recorded" ? null : receipt.schemaVersion;
      required.latencyMs = detail.latencyMs; required.retryCount = detail.retryCount;
      required.paginationComplete = receipt.paginationComplete;
    }
    var started = Date.parse(text(receipt.startedAt)), completed = Date.parse(text(receipt.completedAt));
    if (Number.isFinite(started) && Number.isFinite(completed) && completed < started) required.timeOrdering = null;
    if (!missingValue(detail.latencyMs) && (typeof detail.latencyMs !== "number" || !Number.isFinite(detail.latencyMs) || detail.latencyMs < 0)) {
      required.latencyFiniteNonNegative = null;
    }
    if (!missingValue(detail.retryCount) && (!Number.isInteger(detail.retryCount) || detail.retryCount < 0)) required.retryCountNonNegative = null;
    var counts = [receipt.recordsSeen, receipt.recordsRetained, receipt.recordsRejected];
    if (counts.some(function (value) { return !missingValue(value) && (!Number.isInteger(value) || value < 0); })) {
      required.recordCountsNonNegative = null;
    }
    if (counts.every(Number.isInteger) && counts[1] + counts[2] !== counts[0]) required.recordCountClosure = null;
    var cap = recordCap(detail);
    if (!missingValue(cap) && (!Number.isInteger(cap) || cap <= 0)) required.recordCapPositive = null;
    if (Number.isInteger(cap) && Number.isInteger(counts[0]) && counts[0] > cap) required.recordCapConsistency = null;
    if (receipt.paginationComplete === false && ["DATA_RETURNED", "SEARCHED_NO_MATCH"].indexOf(outcome) >= 0) {
      required.paginationOutcomeConsistency = null;
    }
    if (outcome === "DATA_RETURNED" && receipt.recordsRetained === 0) required.dataOutcomeRecordConsistency = null;
    if (outcome === "SEARCHED_NO_MATCH" && !missingValue(receipt.recordsRetained) && receipt.recordsRetained !== 0) {
      required.dataOutcomeRecordConsistency = null;
    }
    if (outcome === "STALE_LAST_GOOD_RETAINED" && !receipt.staleReceiptId) required.staleReceiptLinkage = null;
    if (requiresExecution && ["DATA_RETURNED", "PARTIAL_RESULT", "SEARCHED_NO_MATCH"].indexOf(outcome) >= 0) {
      required.responseChecksumOrImmutableArtifactReference = validHash(receipt.responseChecksumSha256) || detail.immutableArtifactReference;
    }
    if (["QUERY_FAILED", "QUERY_BLOCKED", "AMBIGUOUS"].indexOf(outcome) >= 0) required.errorCategory = detail.errorCategory;
    var missing = Object.keys(required).filter(function (key) { return missingValue(required[key]); }).sort();
    return {complete: missing.length === 0, missingFields: missing};
  }
  function receiptExtensionsForCandidate(candidate, receipts) {
    var compatibility = candidate.compatibility_provenance || {};
    var raw = compatibility.receiptProvenance || compatibility.receiptExtensions || [];
    if (!Array.isArray(raw)) throw new Error("Receipt provenance must be an array.");
    var receiptById = {}, detailById = {};
    receipts.forEach(function (row) {
      if (!row.receiptId || receiptById[row.receiptId]) throw new Error("Persisted receipt IDs must be present and unique.");
      receiptById[row.receiptId] = row;
    });
    raw.forEach(function (row) {
      if (!row || !row.receiptId || detailById[row.receiptId]) throw new Error("Receipt-provenance IDs must be present and unique.");
      detailById[row.receiptId] = row;
    });
    var receiptIds = Object.keys(receiptById).sort(), detailIds = Object.keys(detailById).sort();
    if (receiptIds.length !== detailIds.length || receiptIds.some(function (id, index) { return id !== detailIds[index]; })) {
      throw new Error("Every persisted receipt must have exactly one provenance extension.");
    }
    return receiptIds.map(function (receiptId) {
      var receipt = receiptById[receiptId], detail = sanitizeReceiptDetail(detailById[receiptId]);
      if (detail.sourceContractId !== receipt.sourceContractId) throw new Error("Receipt provenance has inconsistent source-contract linkage.");
      detail.completeness = receiptCompleteness(receipt, detail);
      return sortObject(detail);
    });
  }

  async function project(candidate, universe) {
    if (!candidate || !candidate.event_id) throw new Error("A CTAS candidate with a stable event UUID is required.");
    var descriptor = candidate.astro_evidence || {}, generatedAt = descriptor.generatedAt;
    if (!generatedAt) throw new Error("The compatibility descriptor has no snapshot generation time.");
    var applicable = new Set((candidate.source_accounting || {}).applicableSourceIds || []);
    var matrixById = {};
    (candidate.source_matrix || []).forEach(function (row) { matrixById[row.sourceContractId] = row; });
    var contracts = (universe && universe.sources || []).filter(function (row) { return applicable.has(row.source_key); })
      .map(function (row) { return sourceContract(row, matrixById[row.source_key], generatedAt); });
    var represented = new Set(contracts.map(function (row) { return row.sourceContractId; }));
    Array.from(applicable).sort().forEach(function (sourceId) {
      if (represented.has(sourceId)) return;
      var matrix = matrixById[sourceId] || {};
      contracts.push(sourceContract({source_key: sourceId, name: matrix.sourceName || sourceId,
        organization_or_facility: matrix.sourceName || sourceId, source_family: "legacy-referenced-source",
        data_types: ["legacy retained public record"], access_mode: "link-only", implementation_state: "link-only",
        rights_or_public_access_basis: "Only already rights-cleared public CTAS rows are projected.",
        authentication_requirement: "Not documented in the legacy row", query_scope: "Legacy retained records only",
        known_limitations: ["The source is referenced by a retained row but is absent from this source-universe version."],
        last_verified: generatedAt}, matrix, generatedAt));
    });
    contracts.sort(function (a, b) { return a.sourceContractId.localeCompare(b.sourceContractId); });
    var receipts = (descriptor.persistedQueryReceipts || []).map(function (row) { return sortObject(sanitizeReceipt(row)); });
    receipts.sort(function (a, b) { return (a.sourceContractId + a.startedAt + a.receiptId).localeCompare(b.sourceContractId + b.startedAt + b.receiptId); });
    receiptExtensionsForCandidate(candidate, receipts);
    var result = {schemaName: descriptor.coreSchemaName || "astro-evidence-core",
      schemaVersion: descriptor.coreSchemaVersion || "0.1.0", generatedAt: generatedAt,
      sourceUniverseVersion: descriptor.sourceUniverseVersion, target: descriptor.target,
      sourceContracts: contracts, queryReceipts: receipts, measurements: await buildMeasurements(candidate),
      conflictSets: descriptor.conflictSets || [], selections: descriptor.selections || [],
      dataProducts: descriptor.dataProducts || [], analysisRuns: descriptor.analysisRuns || []};
    return sortObject(result);
  }

  function entityRows(document_) {
    var targetId = document_.target.targetId, rows = [];
    function add(type, id, source, property, valueKind, value, unit, ucd, timeValue, status, payload) {
      var numericValue = typeof value === "number" && Number.isFinite(value) ? value : null;
      var timeCoordinate = payload && payload.time || payload && payload.observedStart || null;
      rows.push({record_type: type, record_id: text(id), target_id: targetId, source_contract_id: text(source),
        property_code: text(property), value_kind: text(valueKind), normalized_value: text(value), normalized_numeric: numericValue,
        normalized_unit: text(unit), ucd: text(ucd), time: text(timeValue),
        normalized_mjd: timeCoordinate && number(timeCoordinate.normalizedMjd),
        ra_deg: type === "target" ? number(document_.target.raDeg) : null,
        dec_deg: type === "target" ? number(document_.target.decDeg) : null,
        uncertainty_positive: type === "measurement" ? number(payload.uncertaintyPositive) : null,
        uncertainty_negative: type === "measurement" ? number(payload.uncertaintyNegative) : null,
        status: text(status),
        payload_json: canonicalJson(payload).trim()});
    }
    add("target", targetId, "", "identity.target", "QUALITATIVE", document_.target.preferredName,
      "", "meta.id", document_.generatedAt, document_.target.identityState, document_.target);
    (document_.target.aliases || []).forEach(function (row) { add("alias", row.sourceRecordId || row.value, row.sourceContractId,
      "identity.alias", "QUALITATIVE", row.value, "", "meta.id", row.assertedAt, row.linkState, row); });
    document_.sourceContracts.forEach(function (row) { add("source_contract", row.sourceContractId, row.sourceContractId,
      "source.contract", "QUALITATIVE", row.sourceName, "", "meta.id", row.lastVerifiedAt, row.implementationState, row); });
    document_.queryReceipts.forEach(function (row) { add("query_receipt", row.receiptId, row.sourceContractId,
      "query.outcome", "QUALITATIVE", row.outcome, "", "meta.code", row.completedAt, row.outcome, row); });
    document_.measurements.forEach(function (row) { add("measurement", row.measurementId, row.sourceContractId,
      row.propertyCode, row.valueKind, row.normalizedValue, row.normalizedUnit, row.ucd,
      row.time && row.time.originalValue, row.activeState, row); });
    document_.conflictSets.forEach(function (row) { add("conflict", row.conflictSetId, "", row.propertyCode,
      "QUALITATIVE", row.relations.join("|"), "", "meta.code", "", row.assessmentState, row); });
    document_.selections.forEach(function (row) { add("selection", row.selectionId, "", row.purpose,
      "QUALITATIVE", row.measurementIds.join("|"), "", "meta.id", row.selectedAt, row.reviewState, row); });
    document_.dataProducts.forEach(function (row) { add("data_product", row.productId, row.sourceContractId,
      row.productType, "QUALITATIVE", row.accessUrl, "", "meta.ref.url",
      row.observedStart && row.observedStart.originalValue, row.rightsState, row); });
    document_.analysisRuns.forEach(function (row) { add("analysis_run", row.analysisRunId, "", row.analysisType,
      "QUALITATIVE", row.resultChecksumSha256, "", "meta.code", row.createdAt, row.status, row); });
    return rows.sort(function (a, b) { return (a.record_type + "\x1f" + a.record_id).localeCompare(b.record_type + "\x1f" + b.record_id); });
  }
  function csvCell(value) { return '"' + text(value).replace(/"/g, '""') + '"'; }
  function toEcsv(document_) {
    var columns = ["record_type", "record_id", "target_id", "source_contract_id", "property_code", "value_kind",
      "normalized_value", "normalized_numeric", "normalized_unit", "ucd", "time", "normalized_mjd",
      "ra_deg", "dec_deg", "uncertainty_positive", "uncertainty_negative", "status", "payload_json"];
    var numericColumns = new Set(["normalized_numeric", "normalized_mjd", "ra_deg", "dec_deg", "uncertainty_positive", "uncertainty_negative"]);
    var descriptions = {record_type: "AstroEvidence entity kind", record_id: "Stable entity identifier", target_id: "Stable CTAS event UUID",
      source_contract_id: "Provider/source contract", property_code: "Scientific property or entity role", value_kind: "Measurement/limit semantics",
      normalized_value: "Projected textual value; source-native value remains in payload_json",
      normalized_numeric: "Projected numeric value when value semantics are numeric", normalized_unit: "Source-native or normalized unit",
      ucd: "IVOA UCD where known", time: "Source-native scientific, query, or assertion time",
      normalized_mjd: "Normalized MJD only when retained or exactly derivable from a source JD",
      ra_deg: "Target ICRS right ascension", dec_deg: "Target ICRS declination",
      uncertainty_positive: "Positive numeric uncertainty", uncertainty_negative: "Negative numeric uncertainty magnitude",
      status: "Outcome or active state",
      payload_json: "Canonical complete AstroEvidence entity JSON"};
    var header = ["# %ECSV 1.0", "# ---", "# datatype:"];
    columns.forEach(function (column) { header.push("# - {name: " + column + ", datatype: " +
      (numericColumns.has(column) ? "float64" : "string") + ", description: " + JSON.stringify(descriptions[column]) + "}"); });
    header.push("# delimiter: ','", "# meta: !!omap", "# - {schema_name: " + JSON.stringify(document_.schemaName) + "}",
      "# - {schema_version: " + JSON.stringify(document_.schemaVersion) + "}", "# - {generated_at: " + JSON.stringify(document_.generatedAt) + "}",
      "# - {source_universe_version: " + JSON.stringify(document_.sourceUniverseVersion) + "}");
    var body = [columns.join(",")].concat(entityRows(document_).map(function (row) {
      return columns.map(function (column) {
        if (numericColumns.has(column)) return row[column] === null || row[column] === undefined ? "" : text(row[column]);
        return csvCell(row[column]);
      }).join(",");
    }));
    return header.concat(body).join("\n") + "\n";
  }
  function xml(value) { return text(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;"); }
  function toVotable(document_) {
    var definitions = [
      {name: "record_type", datatype: "char", arraysize: "*", ucd: "meta.code.class"},
      {name: "record_id", datatype: "char", arraysize: "*", ucd: "meta.id"},
      {name: "target_id", datatype: "char", arraysize: "*", ucd: "meta.id"},
      {name: "source_contract_id", datatype: "char", arraysize: "*", ucd: "meta.id"},
      {name: "property_code", datatype: "char", arraysize: "*", ucd: "meta.code"},
      {name: "value_kind", datatype: "char", arraysize: "*", ucd: "meta.code"},
      {name: "normalized_value", datatype: "char", arraysize: "*"},
      {name: "normalized_numeric", datatype: "double"},
      {name: "normalized_unit", datatype: "char", arraysize: "*", ucd: "meta.unit"},
      {name: "ucd", datatype: "char", arraysize: "*", ucd: "meta.ucd"},
      {name: "time", datatype: "char", arraysize: "*", ucd: "time.epoch"},
      {name: "normalized_mjd", datatype: "double", unit: "d", ucd: "time.epoch"},
      {name: "ra_deg", datatype: "double", unit: "deg", ucd: "pos.eq.ra"},
      {name: "dec_deg", datatype: "double", unit: "deg", ucd: "pos.eq.dec"},
      {name: "uncertainty_positive", datatype: "double", ucd: "stat.error;stat.max"},
      {name: "uncertainty_negative", datatype: "double", ucd: "stat.error;stat.min"},
      {name: "status", datatype: "char", arraysize: "*", ucd: "meta.code"},
      {name: "payload_json", datatype: "char", arraysize: "*"}
    ];
    var columns = definitions.map(function (definition) { return definition.name; });
    var numericColumns = new Set(definitions.filter(function (definition) { return definition.datatype === "double"; })
      .map(function (definition) { return definition.name; }));
    var fields = definitions.map(function (definition) {
      return '<FIELD name="' + definition.name + '" datatype="' + definition.datatype + '"' +
        (definition.arraysize ? ' arraysize="' + definition.arraysize + '"' : "") +
        (definition.unit ? ' unit="' + definition.unit + '"' : "") +
        (definition.ucd ? ' ucd="' + definition.ucd + '"' : "") + '/>';
    }).join("");
    var rows = entityRows(document_).map(function (row) {
      return "<TR>" + columns.map(function (column) {
        var value = row[column];
        return "<TD>" + (numericColumns.has(column) && (value === null || value === undefined) ? "" : xml(value)) + "</TD>";
      }).join("") + "</TR>";
    }).join("");
    return '<?xml version="1.0" encoding="UTF-8"?>\n<VOTABLE xmlns="http://www.ivoa.net/xml/VOTable/v1.3" version="1.5">' +
      '<DESCRIPTION>Deterministic CTAS AstroEvidence dossier export</DESCRIPTION><COOSYS ID="ICRS" system="ICRS" epoch="J2000"/>' +
      '<RESOURCE type="results"><INFO name="timeSemantics" value="Per-record time representation is retained in payload_json; no global TIMESYS is asserted."/>' +
      '<INFO name="schemaName" value="' + xml(document_.schemaName) + '"/><INFO name="schemaVersion" value="' + xml(document_.schemaVersion) + '"/>' +
      '<INFO name="generatedAt" value="' + xml(document_.generatedAt) + '"/><TABLE name="astro_evidence_entities">' + fields +
      "<DATA><TABLEDATA>" + rows + "</TABLEDATA></DATA></TABLE></RESOURCE></VOTABLE>\n";
  }
  async function buildExportBundle(candidate, universe) {
    var document_ = await project(candidate, universe), json = canonicalJson(document_), ecsv = toEcsv(document_), votable = toVotable(document_);
    var slug = text(candidate.name || "ctas-event").replace(/[^A-Za-z0-9._-]+/g, "-") + "-" + candidate.event_id.slice(0, 8);
    var files = {};
    files[slug + ".json"] = {contentType: "application/json", content: json};
    files[slug + ".ecsv"] = {contentType: "text/plain;charset=utf-8", content: ecsv};
    files[slug + ".vot"] = {contentType: "application/x-votable+xml", content: votable};
    var fileEntries = {};
    for (var name of Object.keys(files).sort()) {
      fileEntries[name] = {sha256: await sha256(files[name].content), bytes: utf8(files[name].content).byteLength,
        contentType: files[name].contentType};
    }
    var releases = {}, accessDates = {}, extensionsById = {};
    var receiptExtensions = receiptExtensionsForCandidate(candidate, document_.queryReceipts);
    receiptExtensions.forEach(function (row) {
      extensionsById[row.receiptId] = row;
      if (row.executionState === "EXECUTED" && row.providerRelease) releases[row.sourceContractId] = row.providerRelease;
    });
    document_.queryReceipts.forEach(function (row) {
      var extension = extensionsById[row.receiptId];
      if (extension && extension.executionState === "EXECUTED" && row.completedAt &&
          (!accessDates[row.sourceContractId] || row.completedAt > accessDates[row.sourceContractId])) {
        accessDates[row.sourceContractId] = row.completedAt;
      }
    });
    var manifest = {schema: "ctas.astro-evidence-export-manifest@1.0.0", generatedAt: document_.generatedAt,
      targetId: candidate.event_id, preferredName: candidate.name, coreSchemaName: document_.schemaName,
      coreSchemaVersion: document_.schemaVersion, sourceUniverseVersion: document_.sourceUniverseVersion,
      projectionVersion: PROJECTION_VERSION, sourceReleases: sortObject(releases), sourceAccessDates: sortObject(accessDates),
      receiptExtensions: sortObject(receiptExtensions),
      selectionExtensions: sortObject(((candidate.compatibility_provenance || {}).selectionProvenance || [])),
      unresolvedEvidence: document_.conflictSets.filter(function (row) { return row.assessmentState === "UNRESOLVED" || row.assessmentState === "AUTOMATED_SCREEN"; })
        .map(function (row) { return {conflictSetId: row.conflictSetId, explanation: row.explanation}; }),
      excludedOrNonDataSources: document_.queryReceipts.filter(function (row) {
        if (["DATA_RETURNED", "PARTIAL_RESULT", "STALE_LAST_GOOD_RETAINED"].indexOf(row.outcome) >= 0) return false;
        return Number(row.recordsRetained || 0) === 0;
      })
        .map(function (row) { return {receiptId: row.receiptId, sourceContractId: row.sourceContractId, outcome: row.outcome, errorCode: row.errorCode}; }),
      files: fileEntries};
    var manifestName = slug + "-manifest.json", manifestContent = canonicalJson(manifest);
    files[manifestName] = {contentType: "application/json", content: manifestContent};
    return {document: document_, manifest: manifest, files: files, manifestName: manifestName};
  }

  return {buildExportBundle: buildExportBundle, canonicalJson: canonicalJson, classificationProperty: classificationProperty,
    entityRows: entityRows, project: project, receiptCompleteness: receiptCompleteness, sha256: sha256,
    stableId: stableId, toEcsv: toEcsv, toVotable: toVotable};
}));
