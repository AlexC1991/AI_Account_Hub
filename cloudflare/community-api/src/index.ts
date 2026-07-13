const MAX_BODY_BYTES = 64 * 1024;
const MAX_PUBLIC_RESPONSE_BYTES = 512 * 1024;
const MAX_CLOCK_SKEW_SECONDS = 300;
const PUBLISH_DAYS = 365;
const SCHEMA_VERSION = 1;

const ENVELOPE_FIELDS = new Set([
  "schemaVersion", "periodStartUtc", "periodEndUtc", "source", "records",
]);
const RECORD_FIELDS = new Set([
  "provider", "modelId", "reasoningEffort", "totalTokens", "completedTasks",
  "activeMs", "edits", "filesChanged", "tests", "commands", "shortBurn",
  "weeklyBurn",
]);
const TEXT_FIELDS = new Set(["provider", "modelId", "reasoningEffort"]);
const encoder = new TextEncoder();

type JsonObject = Record<string, unknown>;
type InstallationRow = { id: string; public_key: string; revoked: number };
type SubmissionRow = {
  receipt_id: string;
  accepted_at: string;
  record_count: number;
  body_sha256: string;
};
type ContributionRecord = {
  provider: string;
  modelId: string;
  reasoningEffort: string;
  totalTokens: number;
  completedTasks: number;
  activeMs: number;
  edits: number;
  filesChanged: number;
  tests: number;
  commands: number;
  shortBurn: number;
  weeklyBurn: number;
};
type CommunityPayload = JsonObject & {
  periodStartUtc: string;
  records: ContributionRecord[];
};
type ModelKey = {
  periodStart: string;
  provider: string;
  modelId: string;
  reasoningEffort: string;
};
type GroupCountRow = {
  provider: string;
  model_id: string;
  reasoning_effort: string;
  contributors: number;
};
type RollupRow = {
  period_start: string;
  provider: string;
  model_id: string;
  reasoning_effort: string;
  total_tokens: number;
  completed_tasks: number;
  active_ms: number;
  edits: number;
  files_changed: number;
  tests: number;
  commands: number;
  short_burn: number;
  weekly_burn: number;
  observations: number;
  contributor_count: number;
};

function responseHeaders(cacheControl = "no-store"): Headers {
  return new Headers({
    "content-type": "application/json; charset=utf-8",
    "cache-control": cacheControl,
    "access-control-allow-origin": "*",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
  });
}

function json(data: unknown, status = 200, cacheControl = "no-store"): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: responseHeaders(cacheControl),
  });
}

function error(code: string, message: string, status: number): Response {
  return json({ error: { code, message } }, status);
}

function clientFailure(
  cause: unknown,
  route: "registration" | "submission" | "withdrawal",
): { message: string; status: number } {
  const message = cause instanceof Error ? cause.message : "";
  const clientSafe = /^(payload|registration|publicKey|unsupported|invalid |request |records|periodStartUtc|submission period|write rate limit|installation |withdrawal request)/.test(message);
  if (!clientSafe) {
    // Do not serialize D1, R2, query, binding, or stack details to a public client.
    console.error(JSON.stringify({ event: "community_internal_error", route }));
    return { message: `${route[0].toUpperCase()}${route.slice(1)} could not be completed.`, status: 503 };
  }
  const status = message.includes("64 KiB") ? 413
    : message.includes("rate limit") ? 429
    : message.includes("nonce") ? 409
    : message.includes("timestamp") || message.includes("signature") || message.includes("registered") ? 401
    : 400;
  return { message, status };
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseJson(body: Uint8Array): unknown {
  try {
    return JSON.parse(new TextDecoder().decode(body));
  } catch {
    throw new Error("request body must contain valid JSON");
  }
}

function assertAllowedKeys(value: JsonObject, allowed: Set<string>, path: string): void {
  const unexpected = Object.keys(value).filter((key) => !allowed.has(key));
  if (unexpected.length > 0) {
    throw new Error(`${path} contains unsupported fields: ${unexpected.sort().join(", ")}`);
  }
}

function finiteNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > Number.MAX_SAFE_INTEGER) {
    throw new Error(`${field} must be a finite non-negative number`);
  }
  return value;
}

function validatePayload(value: unknown): CommunityPayload {
  if (!isObject(value)) throw new Error("payload must be an object");
  assertAllowedKeys(value, ENVELOPE_FIELDS, "payload");
  if (value.schemaVersion !== SCHEMA_VERSION || value.source !== "ai-account-hub") {
    throw new Error("unsupported schema version or source");
  }
  const startMs = Date.parse(String(value.periodStartUtc));
  const endMs = Date.parse(String(value.periodEndUtc));
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) {
    throw new Error("periodStartUtc and periodEndUtc must be ISO-8601 timestamps");
  }
  const startDate = new Date(startMs);
  const startDay = startDate.toISOString().slice(0, 10);
  const exactUtcStart = Date.parse(`${startDay}T00:00:00.000Z`);
  const now = Date.now();
  if (startMs !== exactUtcStart || endMs !== startMs + 24 * 60 * 60 * 1000) {
    throw new Error("submission period must be one exact UTC day");
  }
  if (startMs < now - 35 * 24 * 60 * 60 * 1000 || startMs > now + MAX_CLOCK_SKEW_SECONDS * 1000) {
    throw new Error("submission period is outside the accepted date window");
  }
  if (!Array.isArray(value.records) || value.records.length < 1 || value.records.length > 100) {
    throw new Error("records must contain between 1 and 100 aggregates");
  }
  const normalizedRecords: ContributionRecord[] = [];
  const recordKeys = new Set<string>();
  for (const [index, candidate] of value.records.entries()) {
    if (!isObject(candidate)) throw new Error(`records[${index}] must be an object`);
    assertAllowedKeys(candidate, RECORD_FIELDS, `records[${index}]`);
    for (const field of RECORD_FIELDS) {
      const item = candidate[field];
      if (TEXT_FIELDS.has(field)) {
        if (field !== "reasoningEffort" && (typeof item !== "string" || item.length < 1)) {
          throw new Error(`records[${index}].${field} is required`);
        }
        if (typeof item !== "string" || item.length > 96) {
          throw new Error(`records[${index}].${field} must be a short string`);
        }
      } else {
        finiteNumber(item, `records[${index}].${field}`);
      }
    }
    const provider = String(candidate.provider).trim().toLowerCase();
    const modelId = String(candidate.modelId).trim();
    const reasoningEffort = String(candidate.reasoningEffort).trim().toLowerCase();
    if (!/^[a-z0-9_-]{1,48}$/.test(provider)) {
      throw new Error(`records[${index}].provider is invalid`);
    }
    if (!/^[A-Za-z0-9][A-Za-z0-9._:+/@ -]{0,95}$/.test(modelId)) {
      throw new Error(`records[${index}].modelId is invalid`);
    }
    if (reasoningEffort && !/^[a-z0-9._:+ -]{1,48}$/.test(reasoningEffort)) {
      throw new Error(`records[${index}].reasoningEffort is invalid`);
    }
    const recordKey = `${provider}\u0000${modelId.toLowerCase()}\u0000${reasoningEffort}`;
    if (recordKeys.has(recordKey)) {
      throw new Error(`records[${index}] duplicates a model and reasoning setting`);
    }
    recordKeys.add(recordKey);
    normalizedRecords.push({
      provider,
      modelId,
      reasoningEffort,
      totalTokens: Number(candidate.totalTokens),
      completedTasks: Number(candidate.completedTasks),
      activeMs: Number(candidate.activeMs),
      edits: Number(candidate.edits),
      filesChanged: Number(candidate.filesChanged),
      tests: Number(candidate.tests),
      commands: Number(candidate.commands),
      shortBurn: Number(candidate.shortBurn),
      weeklyBurn: Number(candidate.weeklyBurn),
    });
  }
  return { ...value, periodStartUtc: String(value.periodStartUtc), records: normalizedRecords };
}

async function readBodyWithLimit(request: Request): Promise<Uint8Array> {
  if (request.body === null) return new Uint8Array();
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > MAX_BODY_BYTES) throw new Error("request body exceeds 64 KiB");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BODY_BYTES) {
      await reader.cancel();
      throw new Error("request body exceeds 64 KiB");
    }
    chunks.push(value);
  }
  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

function base64Bytes(value: string, expectedLength?: number): Uint8Array {
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(value) || value.length > 512) {
    throw new Error("invalid base64 value");
  }
  let decoded: string;
  try {
    decoded = atob(value);
  } catch {
    throw new Error("invalid base64 value");
  }
  const bytes = Uint8Array.from(decoded, (character) => character.charCodeAt(0));
  if (expectedLength !== undefined && bytes.byteLength !== expectedLength) {
    throw new Error("invalid signature length");
  }
  return bytes;
}

function hex(bytes: ArrayBuffer): string {
  return [...new Uint8Array(bytes)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

function ownedBuffer(bytes: Uint8Array): ArrayBuffer {
  const buffer = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(buffer).set(bytes);
  return buffer;
}

async function sha256Hex(value: Uint8Array | ArrayBuffer): Promise<string> {
  const buffer = value instanceof Uint8Array ? ownedBuffer(value) : value;
  return hex(await crypto.subtle.digest("SHA-256", buffer));
}

function authTimestamp(request: Request): string {
  const raw = request.headers.get("x-aih-timestamp") || "";
  if (!/^\d{10}$/.test(raw)) throw new Error("invalid request timestamp");
  const difference = Math.abs(Math.floor(Date.now() / 1000) - Number(raw));
  if (difference > MAX_CLOCK_SKEW_SECONDS) throw new Error("request timestamp has expired");
  return raw;
}

function authNonce(request: Request): string {
  const nonce = request.headers.get("x-aih-nonce") || "";
  if (!/^[A-Za-z0-9_-]{24,128}$/.test(nonce)) throw new Error("invalid request nonce");
  return nonce;
}

async function importPublicKey(publicKey: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "spki",
    ownedBuffer(base64Bytes(publicKey)),
    { name: "ECDSA", namedCurve: "P-256" },
    false,
    ["verify"],
  );
}

async function verifySignature(publicKey: string, signature: string, canonical: string): Promise<void> {
  const valid = await crypto.subtle.verify(
    { name: "ECDSA", hash: "SHA-256" },
    await importPublicKey(publicKey),
    ownedBuffer(base64Bytes(signature, 64)),
    ownedBuffer(encoder.encode(canonical)),
  );
  if (!valid) throw new Error("request signature is invalid");
}

async function claimNonce(env: Env, installationId: string, nonce: string): Promise<void> {
  const result = await env.DB.prepare(
    "INSERT OR IGNORE INTO nonces (installation_id, nonce, seen_at) VALUES (?1, ?2, ?3)",
  ).bind(installationId, nonce, new Date().toISOString()).run();
  if (Number(result.meta.changes || 0) !== 1) throw new Error("request nonce was already used");
  await env.DB.prepare(
    "DELETE FROM nonces WHERE seen_at < datetime('now', '-1 day')",
  ).run();
}

async function registerInstallation(request: Request, env: Env): Promise<Response> {
  if (request.headers.get("content-type")?.split(";", 1)[0].trim() !== "application/json") {
    return error("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json.", 415);
  }
  try {
    const body = await readBodyWithLimit(request);
    const candidate = parseJson(body);
    if (!isObject(candidate)) throw new Error("registration must be an object");
    assertAllowedKeys(candidate, new Set(["publicKey"]), "registration");
    if (typeof candidate.publicKey !== "string") throw new Error("publicKey is required");
    const publicBytes = base64Bytes(candidate.publicKey);
    const installationId = (await sha256Hex(publicBytes)).slice(0, 32);
    const { success } = await env.REGISTER_RATE_LIMITER.limit({ key: installationId });
    if (!success) return error("RATE_LIMITED", "Too many registration requests.", 429);
    const timestamp = authTimestamp(request);
    const nonce = authNonce(request);
    const signature = request.headers.get("x-aih-signature") || "";
    await verifySignature(
      candidate.publicKey,
      signature,
      `AIH1-REGISTER\n${candidate.publicKey}\n${timestamp}\n${nonce}`,
    );
    const now = new Date().toISOString();
    await env.DB.prepare(
      "INSERT INTO installations (id, public_key, created_at, last_seen_at, revoked) " +
      "VALUES (?1, ?2, ?3, ?3, 0) ON CONFLICT(id) DO UPDATE SET " +
      "public_key = excluded.public_key, last_seen_at = excluded.last_seen_at, revoked = 0",
    ).bind(installationId, candidate.publicKey, now).run();
    await claimNonce(env, installationId, nonce);
    return json({ registered: true, installationId, registeredAtUtc: now }, 201);
  } catch (cause) {
    const failure = clientFailure(cause, "registration");
    return error("REGISTRATION_REJECTED", failure.message, failure.status);
  }
}

async function authorizeSignedRequest(
  request: Request,
  env: Env,
  body: Uint8Array,
  allowRevoked = false,
): Promise<InstallationRow> {
  const installationId = request.headers.get("x-aih-installation") || "";
  if (!/^[a-f0-9]{32}$/.test(installationId)) throw new Error("invalid installation identifier");
  const { success } = await env.WRITE_RATE_LIMITER.limit({ key: installationId });
  if (!success) throw new Error("write rate limit exceeded");
  const timestamp = authTimestamp(request);
  const nonce = authNonce(request);
  const suppliedHash = request.headers.get("x-aih-body-sha256") || "";
  const actualHash = await sha256Hex(body);
  if (!/^[a-f0-9]{64}$/.test(suppliedHash) || suppliedHash !== actualHash) {
    throw new Error("request body hash does not match");
  }
  const installation = await env.DB.prepare(
    "SELECT id, public_key, revoked FROM installations WHERE id = ?1",
  ).bind(installationId).first<InstallationRow>();
  if (!installation || (!allowRevoked && installation.revoked !== 0)) {
    throw new Error("installation is not registered or has been withdrawn");
  }
  const path = new URL(request.url).pathname;
  const canonical = [
    "AIH1", request.method, path, installationId, timestamp, nonce, suppliedHash,
  ].join("\n");
  await verifySignature(
    installation.public_key,
    request.headers.get("x-aih-signature") || "",
    canonical,
  );
  await claimNonce(env, installationId, nonce);
  await env.DB.prepare(
    "UPDATE installations SET last_seen_at = ?1 WHERE id = ?2",
  ).bind(new Date().toISOString(), installationId).run();
  return installation;
}

function modelKey(value: ModelKey): string {
  return [value.periodStart, value.provider, value.modelId, value.reasoningEffort].join("\u0000");
}

function recordModelKey(periodStart: string, record: ContributionRecord): ModelKey {
  return {
    periodStart,
    provider: record.provider,
    modelId: record.modelId,
    reasoningEffort: record.reasoningEffort,
  };
}

async function ensureContributionRows(
  env: Env,
  receiptId: string,
  installationId: string,
  periodStart: string,
  records: ContributionRecord[],
): Promise<ModelKey[]> {
  const statements = records.map((record) => env.DB.prepare(
    "INSERT OR IGNORE INTO daily_contributions " +
    "(receipt_id, installation_id, period_start, provider, model_id, reasoning_effort, " +
    "total_tokens, completed_tasks, active_ms, edits, files_changed, tests, commands, " +
    "short_burn, weekly_burn) VALUES " +
    "(?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
  ).bind(
    receiptId,
    installationId,
    periodStart,
    record.provider,
    record.modelId,
    record.reasoningEffort,
    record.totalTokens,
    record.completedTasks,
    record.activeMs,
    record.edits,
    record.filesChanged,
    record.tests,
    record.commands,
    record.shortBurn,
    record.weeklyBurn,
  ));
  for (let offset = 0; offset < statements.length; offset += 50) {
    await env.DB.batch(statements.slice(offset, offset + 50));
  }
  return records.map((record) => recordModelKey(periodStart, record));
}

async function recomputeModelRollups(env: Env, keys: ModelKey[]): Promise<void> {
  const unique = [...new Map(keys.map((key) => [modelKey(key), key])).values()];
  const updatedAt = new Date().toISOString();
  for (let offset = 0; offset < unique.length; offset += 25) {
    const statements: D1PreparedStatement[] = [];
    for (const key of unique.slice(offset, offset + 25)) {
      statements.push(
        env.DB.prepare(
          "DELETE FROM daily_model_rollups WHERE period_start = ?1 AND provider = ?2 " +
          "AND model_id = ?3 AND reasoning_effort = ?4",
        ).bind(key.periodStart, key.provider, key.modelId, key.reasoningEffort),
        env.DB.prepare(
          "INSERT INTO daily_model_rollups " +
          "(period_start, provider, model_id, reasoning_effort, total_tokens, completed_tasks, " +
          "active_ms, edits, files_changed, tests, commands, short_burn, weekly_burn, " +
          "observations, contributor_count, updated_at) " +
          "SELECT period_start, provider, model_id, reasoning_effort, " +
          "SUM(total_tokens), SUM(completed_tasks), SUM(active_ms), SUM(edits), " +
          "SUM(files_changed), SUM(tests), SUM(commands), SUM(short_burn), SUM(weekly_burn), " +
          "COUNT(*), COUNT(DISTINCT installation_id), ?5 FROM daily_contributions " +
          "WHERE period_start = ?1 AND provider = ?2 AND model_id = ?3 AND reasoning_effort = ?4 " +
          "GROUP BY period_start, provider, model_id, reasoning_effort",
        ).bind(key.periodStart, key.provider, key.modelId, key.reasoningEffort, updatedAt),
      );
    }
    await env.DB.batch(statements);
  }
}

async function rebuildAllRollups(env: Env): Promise<void> {
  const updatedAt = new Date().toISOString();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM daily_model_rollups"),
    env.DB.prepare(
      "INSERT INTO daily_model_rollups " +
      "(period_start, provider, model_id, reasoning_effort, total_tokens, completed_tasks, " +
      "active_ms, edits, files_changed, tests, commands, short_burn, weekly_burn, " +
      "observations, contributor_count, updated_at) " +
      "SELECT period_start, provider, model_id, reasoning_effort, " +
      "SUM(total_tokens), SUM(completed_tasks), SUM(active_ms), SUM(edits), " +
      "SUM(files_changed), SUM(tests), SUM(commands), SUM(short_burn), SUM(weekly_burn), " +
      "COUNT(*), COUNT(DISTINCT installation_id), ?1 FROM daily_contributions " +
      "GROUP BY period_start, provider, model_id, reasoning_effort",
    ).bind(updatedAt),
  ]);
}

function minimumPublicContributors(env: Env): number {
  const configured = Number(env.MIN_PUBLIC_CONTRIBUTORS || 10);
  return Math.max(3, Math.min(Number.isFinite(configured) ? Math.floor(configured) : 10, 1000));
}

function displayName(value: string): string {
  const fixed: Record<string, string> = {
    gpt: "GPT", claude: "Claude", codex: "Codex", opus: "Opus",
    sonnet: "Sonnet", haiku: "Haiku", xhigh: "XHigh", high: "High",
    medium: "Medium", low: "Low", ultra: "Ultra", standard: "Standard",
  };
  return value.split(/[\s_-]+/).filter(Boolean).map((part) => {
    const lower = part.toLowerCase();
    return fixed[lower] || (/[A-Z]/.test(part) ? part : part[0].toUpperCase() + part.slice(1));
  }).join(" ");
}

function ratio(numerator: number, denominator: number): number {
  return denominator > 0 ? Math.round((numerator / denominator) * 10000) / 10000 : 0;
}

async function publishCommunityModels(env: Env): Promise<JsonObject> {
  const minimum = minimumPublicContributors(env);
  const cutoffDate = new Date();
  cutoffDate.setUTCDate(cutoffDate.getUTCDate() - (PUBLISH_DAYS - 1));
  const cutoff = cutoffDate.toISOString().slice(0, 10);
  const counts = await env.DB.prepare(
    "SELECT provider, model_id, reasoning_effort, " +
    "COUNT(DISTINCT installation_id) AS contributors FROM daily_contributions " +
    "WHERE period_start >= ?1 GROUP BY provider, model_id, reasoning_effort " +
    "ORDER BY COUNT(*) DESC LIMIT 100",
  ).bind(cutoff).all<GroupCountRow>();
  const collection = await env.DB.prepare(
    "SELECT COUNT(DISTINCT installation_id) AS contributors, " +
    "COUNT(DISTINCT receipt_id) AS submissions FROM daily_contributions " +
    "WHERE period_start >= ?1",
  ).bind(cutoff).first<{ contributors: number; submissions: number }>();
  const providerCounts = await env.DB.prepare(
    "SELECT provider, COUNT(DISTINCT installation_id) AS contributors " +
    "FROM daily_contributions WHERE period_start >= ?1 GROUP BY provider",
  ).bind(cutoff).all<{ provider: string; contributors: number }>();
  const rollups = await env.DB.prepare(
    "SELECT period_start, provider, model_id, reasoning_effort, total_tokens, " +
    "completed_tasks, active_ms, edits, files_changed, tests, commands, short_burn, " +
    "weekly_burn, observations, contributor_count FROM daily_model_rollups " +
    "WHERE period_start >= ?1 AND contributor_count >= ?2 " +
    "ORDER BY period_start ASC LIMIT 10000",
  ).bind(cutoff, minimum).all<RollupRow>();

  const qualifying = new Map(
    counts.results
      .filter((row) => Number(row.contributors || 0) >= minimum)
      .map((row) => [
        [row.provider, row.model_id, row.reasoning_effort].join("\u0000"),
        Number(row.contributors || 0),
      ]),
  );
  const groups = new Map<string, JsonObject>();
  for (const row of rollups.results) {
    const key = [row.provider, row.model_id, row.reasoning_effort].join("\u0000");
    const contributors = qualifying.get(key);
    if (!contributors) continue;
    let group = groups.get(key);
    if (!group) {
      const modelName = displayName(row.model_id);
      const reasoningName = displayName(row.reasoning_effort);
      group = {
        provider: row.provider,
        modelId: row.model_id,
        modelName,
        modelLabel: reasoningName ? `${modelName} - ${reasoningName}` : modelName,
        reasoningEffort: row.reasoning_effort,
        reasoningEffortName: reasoningName,
        filterKey: `community|${row.provider}|${row.model_id}|${row.reasoning_effort}`,
        totalTokens: 0,
        completedTasks: 0,
        shortBurn: 0,
        weeklyBurn: 0,
        observations: 0,
        contributorDays: 0,
        contributors,
        aggregation: "per-contributor-day mean",
        _weightedTokens: 0,
        _weightedTasks: 0,
        _weightedShortBurn: 0,
        _weightedWeeklyBurn: 0,
        days: {},
      };
      groups.set(key, group);
    }
    const tasks = Number(row.completed_tasks || 0);
    const tokens = Number(row.total_tokens || 0);
    const shortBurn = Number(row.short_burn || 0);
    const weeklyBurn = Number(row.weekly_burn || 0);
    const dayContributors = Math.max(1, Number(row.contributor_count || 0));
    const meanTokens = ratio(tokens, dayContributors);
    const meanTasks = ratio(tasks, dayContributors);
    const meanShortBurn = ratio(shortBurn, dayContributors);
    const meanWeeklyBurn = ratio(weeklyBurn, dayContributors);
    (group.days as JsonObject)[row.period_start] = {
      tokens: meanTokens,
      tasks: meanTasks,
      shortBurn: meanShortBurn,
      weeklyBurn: meanWeeklyBurn,
      tasksPerSession: shortBurn > 0 ? ratio(tasks * 100, shortBurn) : 0,
      tokensPerTask: ratio(tokens, tasks),
      weeklyBurnPerTask: ratio(weeklyBurn, tasks),
      observations: tasks,
      contributorDays: Number(row.observations || 0),
      contributors: dayContributors,
    };
    // Absolute chart values represent a typical contributing installation on
    // each day. The hidden weighted sums retain one equal installation-day
    // contribution for ratios, so neither extra provider accounts nor changing
    // daily cohort size silently inflate the public comparison.
    group.totalTokens = Number(group.totalTokens) + meanTokens;
    group.completedTasks = Number(group.completedTasks) + meanTasks;
    group.shortBurn = Number(group.shortBurn) + meanShortBurn;
    group.weeklyBurn = Number(group.weeklyBurn) + meanWeeklyBurn;
    group.observations = Number(group.observations) + tasks;
    group.contributorDays = Number(group.contributorDays) + Number(row.observations || 0);
    group._weightedTokens = Number(group._weightedTokens) + tokens;
    group._weightedTasks = Number(group._weightedTasks) + tasks;
    group._weightedShortBurn = Number(group._weightedShortBurn) + shortBurn;
    group._weightedWeeklyBurn = Number(group._weightedWeeklyBurn) + weeklyBurn;
  }

  const publishedGroups = [...groups.values()];
  for (const group of publishedGroups) {
    const tasks = Number(group._weightedTasks || 0);
    const tokens = Number(group._weightedTokens || 0);
    const shortBurn = Number(group._weightedShortBurn || 0);
    const weeklyBurn = Number(group._weightedWeeklyBurn || 0);
    group.tasksPerSession = shortBurn > 0 ? ratio(tasks * 100, shortBurn) : 0;
    group.tokensPerTask = ratio(tokens, tasks);
    group.weeklyBurnPerTask = ratio(weeklyBurn, tasks);
    group.normalized = {
      tokensPerCompletedTask: group.tokensPerTask,
      tasksPerMillionTokens: tokens > 0 ? ratio(tasks * 1_000_000, tokens) : 0,
      tasksPerSession: group.tasksPerSession,
    };
    delete group._weightedTokens;
    delete group._weightedTasks;
    delete group._weightedShortBurn;
    delete group._weightedWeeklyBurn;
  }
  publishedGroups.sort((left, right) => Number(right.observations) - Number(left.observations));

  const payload: JsonObject = {
    schemaVersion: SCHEMA_VERSION,
    mode: env.ENVIRONMENT,
    dataSource: publishedGroups.length ? "real-community" : "real-pending",
    generatedAtUtc: new Date().toISOString(),
    periodDays: PUBLISH_DAYS,
    minimumContributors: minimum,
    collectionContributors: Number(collection?.contributors || 0),
    collectionSubmissions: Number(collection?.submissions || 0),
    contributors: Number(collection?.contributors || 0),
    providerContributors: Object.fromEntries(
      providerCounts.results
        .filter((row) => Number(row.contributors || 0) >= minimum)
        .map((row) => [row.provider, Number(row.contributors || 0)]),
    ),
    observedTasks: publishedGroups.reduce(
      (total, group) => total + Number(group.observations || 0), 0,
    ),
    groups: publishedGroups,
  };
  let body = JSON.stringify(payload);
  while (encoder.encode(body).byteLength > MAX_PUBLIC_RESPONSE_BYTES && publishedGroups.length > 0) {
    publishedGroups.pop();
    payload.observedTasks = publishedGroups.reduce(
      (total, group) => total + Number(group.observations || 0), 0,
    );
    body = JSON.stringify(payload);
  }
  if (encoder.encode(body).byteLength > MAX_PUBLIC_RESPONSE_BYTES) {
    throw new Error("published aggregate exceeds response limit");
  }
  await env.TELEMETRY_BUCKET.put(env.PUBLISHED_REAL_MODELS_KEY, body, {
    httpMetadata: { contentType: "application/json; charset=utf-8" },
    customMetadata: {
      schemaVersion: String(SCHEMA_VERSION),
      dataSource: String(payload.dataSource),
      generatedAtUtc: String(payload.generatedAtUtc),
    },
  });
  return payload;
}

async function acceptSubmission(request: Request, env: Env): Promise<Response> {
  if (request.headers.get("content-type")?.split(";", 1)[0].trim() !== "application/json") {
    return error("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json.", 415);
  }
  try {
    const body = await readBodyWithLimit(request);
    const installation = await authorizeSignedRequest(request, env, body);
    const payload = validatePayload(parseJson(body));
    const periodStart = String(payload.periodStartUtc).slice(0, 10);
    const bodyHash = await sha256Hex(body);
    const existing = await env.DB.prepare(
      "SELECT receipt_id, accepted_at, record_count, body_sha256 FROM submissions " +
      "WHERE installation_id = ?1 AND period_start = ?2",
    ).bind(installation.id, periodStart).first<SubmissionRow>();
    if (existing) {
      if (existing.body_sha256 === bodyHash) {
        const keys = await ensureContributionRows(
          env, existing.receipt_id, installation.id, periodStart, payload.records,
        );
        await recomputeModelRollups(env, keys);
      }
      const publication = await publishCommunityModels(env);
      return json({
        accepted: true,
        duplicate: true,
        receiptId: existing.receipt_id,
        acceptedAtUtc: existing.accepted_at,
        recordCount: existing.record_count,
        publicationSource: publication.dataSource,
      }, 200);
    }

    const receiptId = crypto.randomUUID();
    const acceptedAtUtc = new Date().toISOString();
    const objectKey = `raw/v1/${installation.id}/${periodStart}/${receiptId}.json`;
    const recordCount = payload.records.length;
    const inserted = await env.DB.prepare(
      "INSERT OR IGNORE INTO submissions " +
      "(receipt_id, installation_id, period_start, accepted_at, object_key, body_sha256, record_count) " +
      "VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
    ).bind(
      receiptId, installation.id, periodStart, acceptedAtUtc, objectKey, bodyHash, recordCount,
    ).run();
    if (Number(inserted.meta.changes || 0) !== 1) {
      const raced = await env.DB.prepare(
        "SELECT receipt_id, accepted_at, record_count, body_sha256 FROM submissions " +
        "WHERE installation_id = ?1 AND period_start = ?2",
      ).bind(installation.id, periodStart).first<SubmissionRow>();
      if (!raced) throw new Error("submission could not be reserved");
      if (raced.body_sha256 === bodyHash) {
        const keys = await ensureContributionRows(
          env, raced.receipt_id, installation.id, periodStart, payload.records,
        );
        await recomputeModelRollups(env, keys);
      }
      const publication = await publishCommunityModels(env);
      return json({
        accepted: true,
        duplicate: true,
        receiptId: raced.receipt_id,
        acceptedAtUtc: raced.accepted_at,
        recordCount: raced.record_count,
        publicationSource: publication.dataSource,
      }, 200);
    }
    let keys: ModelKey[] = [];
    try {
      keys = await ensureContributionRows(
        env, receiptId, installation.id, periodStart, payload.records,
      );
      await recomputeModelRollups(env, keys);
      await env.TELEMETRY_BUCKET.put(objectKey, body, {
        httpMetadata: { contentType: "application/json; charset=utf-8" },
        customMetadata: {
          schemaVersion: String(SCHEMA_VERSION), receiptId, acceptedAtUtc,
        },
      });
    } catch (cause) {
      await env.TELEMETRY_BUCKET.delete(objectKey);
      await env.DB.prepare("DELETE FROM submissions WHERE receipt_id = ?1").bind(receiptId).run();
      await recomputeModelRollups(env, keys);
      throw cause;
    }
    const publication = await publishCommunityModels(env);
    return json({
      accepted: true,
      duplicate: false,
      receiptId,
      acceptedAtUtc,
      recordCount,
      publicationSource: publication.dataSource,
    }, 202);
  } catch (cause) {
    const failure = clientFailure(cause, "submission");
    return error("SUBMISSION_REJECTED", failure.message, failure.status);
  }
}

async function withdrawInstallation(request: Request, env: Env): Promise<Response> {
  try {
    const body = await readBodyWithLimit(request);
    if (body.byteLength !== 0) throw new Error("withdrawal request body must be empty");
    const installation = await authorizeSignedRequest(request, env, body, true);
    await env.DB.prepare("UPDATE installations SET revoked = 1 WHERE id = ?1")
      .bind(installation.id).run();
    const submissions = await env.DB.prepare(
      "SELECT object_key FROM submissions WHERE installation_id = ?1",
    ).bind(installation.id).all<{ object_key: string }>();
    const keys = submissions.results.map((row) => row.object_key);
    for (let offset = 0; offset < keys.length; offset += 1000) {
      await env.TELEMETRY_BUCKET.delete(keys.slice(offset, offset + 1000));
    }
    await env.DB.batch([
      env.DB.prepare("DELETE FROM submissions WHERE installation_id = ?1").bind(installation.id),
      env.DB.prepare("DELETE FROM nonces WHERE installation_id = ?1").bind(installation.id),
    ]);
    // Rebuilding from surviving private contribution rows makes withdrawal
    // retryable even if a previous publication attempt stopped halfway.
    await rebuildAllRollups(env);
    const publication = await publishCommunityModels(env);
    await env.DB.batch([
      env.DB.prepare("DELETE FROM nonces WHERE installation_id = ?1").bind(installation.id),
      env.DB.prepare("DELETE FROM installations WHERE id = ?1").bind(installation.id),
    ]);
    return json({
      withdrawn: true,
      withdrawnAtUtc: new Date().toISOString(),
      deletedSubmissions: keys.length,
      publicationSource: publication.dataSource,
    }, 200);
  } catch (cause) {
    const failure = clientFailure(cause, "withdrawal");
    return error("WITHDRAWAL_REJECTED", failure.message, failure.status);
  }
}

async function readPublishedPayload(env: Env, key: string): Promise<JsonObject | null> {
  const object = await env.TELEMETRY_BUCKET.get(key);
  if (object === null) return null;
  if (object.size > MAX_PUBLIC_RESPONSE_BYTES) {
    throw new Error("published result exceeds response limit");
  }
  const parsed = JSON.parse(await object.text());
  if (!isObject(parsed) || !Array.isArray(parsed.groups)) {
    throw new Error("published result has an invalid shape");
  }
  return parsed;
}

async function publishedModels(env: Env): Promise<Response> {
  try {
    const real = await readPublishedPayload(env, env.PUBLISHED_REAL_MODELS_KEY);
    if (real && (real.groups as unknown[]).length > 0) {
      return json(real, 200, "public, max-age=300, stale-while-revalidate=60");
    }
    const sample = await readPublishedPayload(env, env.PUBLISHED_MODELS_KEY);
    if (sample) {
      sample.dataSource = "synthetic-staging";
      sample.realCollectionActive = true;
      sample.minimumContributors = Number(real?.minimumContributors || minimumPublicContributors(env));
      sample.collectionContributors = Number(real?.collectionContributors || 0);
      sample.collectionSubmissions = Number(real?.collectionSubmissions || 0);
      return json(sample, 200, "public, max-age=60, stale-while-revalidate=30");
    }
    return json(real || {
      schemaVersion: SCHEMA_VERSION,
      mode: env.ENVIRONMENT,
      dataSource: "real-pending",
      generatedAtUtc: null,
      minimumContributors: minimumPublicContributors(env),
      collectionContributors: 0,
      collectionSubmissions: 0,
      contributors: 0,
      observedTasks: 0,
      groups: [],
    }, 200, "public, max-age=60");
  } catch {
    return error(
      "PUBLISHED_RESULT_TOO_LARGE",
      "Published community result is unavailable or invalid.",
      502,
    );
  }
}

export default {
  async fetch(request, env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      const headers = responseHeaders("public, max-age=86400");
      headers.set("access-control-allow-methods", "GET, POST, DELETE, OPTIONS");
      headers.set(
        "access-control-allow-headers",
        "content-type, x-aih-installation, x-aih-timestamp, x-aih-nonce, " +
        "x-aih-body-sha256, x-aih-signature",
      );
      return new Response(null, { status: 204, headers });
    }
    if (request.method === "GET" && url.pathname === "/v1/health") {
      return json({ status: "ok", mode: env.ENVIRONMENT, storage: "r2+d1-bindings" });
    }
    if (request.method === "GET" && url.pathname === "/v1/schema") {
      return json({
        schemaVersion: SCHEMA_VERSION,
        maxBodyBytes: MAX_BODY_BYTES,
        maxRecords: 100,
        deployedIngestionEnabled: true,
        signing: "ECDSA-P256-SHA256",
        automaticPublication: true,
        minimumPublicContributors: minimumPublicContributors(env),
      }, 200, "public, max-age=3600");
    }
    if (request.method === "GET" && url.pathname === "/v1/community/models") {
      return publishedModels(env);
    }
    if (request.method === "POST" && url.pathname === "/v1/installations") {
      return registerInstallation(request, env);
    }
    if (request.method === "POST" && url.pathname === "/v1/submissions") {
      return acceptSubmission(request, env);
    }
    if (request.method === "DELETE" && url.pathname === "/v1/installations/me") {
      return withdrawInstallation(request, env);
    }
    const known = new Set([
      "/v1/health", "/v1/schema", "/v1/community/models", "/v1/installations",
      "/v1/submissions", "/v1/installations/me",
    ]);
    if (known.has(url.pathname)) {
      return error("METHOD_NOT_ALLOWED", "Method not allowed for this route.", 405);
    }
    return error("NOT_FOUND", "Route not found.", 404);
  },
  async scheduled(_controller, env, ctx): Promise<void> {
    // Hourly reconciliation repairs any interrupted write/publication boundary
    // without exposing a public administrative endpoint.
    ctx.waitUntil((async () => {
      await rebuildAllRollups(env);
      await publishCommunityModels(env);
    })());
  },
} satisfies ExportedHandler<Env>;
