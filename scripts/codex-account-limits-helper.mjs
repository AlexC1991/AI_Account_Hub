import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";

const [codexPath, codexHome, workspace = process.cwd()] = process.argv.slice(2);
const action = process.argv[5] || "read";

if (!codexPath || !codexHome) {
  console.log(JSON.stringify({ ok: false, error: "Usage: node codex-account-limits-helper.mjs <codex.exe> <CODEX_HOME> [workspace]" }));
  process.exit(0);
}

const child = spawn(codexPath, ["app-server", "--stdio"], {
  cwd: workspace,
  env: { ...process.env, CODEX_HOME: codexHome },
  stdio: ["pipe", "pipe", "pipe"],
  windowsHide: true,
});

let buffer = "";
let nextId = 1;
const pending = new Map();
// A single read is not trustworthy immediately after app-server startup: the
// server can interleave an empty newly-created window with the enforced one.
// Five samples give us an odd-sized majority without making refreshes excessive.
const RATE_LIMIT_SAMPLE_COUNT = 5;
const RATE_LIMIT_SAMPLE_PAUSE_MS = 120;

function send(method, params = undefined) {
  const id = nextId++;
  child.stdin.write(`${JSON.stringify({ id, method, params })}\n`);
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject, method });
  });
}

function safeNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function epochToIso(value) {
  const n = safeNumber(value);
  if (n === null) return null;
  const millis = n > 1_000_000_000_000 ? n : n * 1000;
  const date = new Date(millis);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function normalizeWindow(window, fallbackLabel) {
  if (!window) return null;
  const duration = safeNumber(window.windowDurationMins);
  const usedPercent = safeNumber(window.usedPercent);
  const resetsAtIso = epochToIso(window.resetsAt);
  let label = fallbackLabel;
  if (duration === 300) label = "5h";
  else if (duration === 10080) label = "Weekly";
  else if (duration) label = `${duration}m`;

  return {
    label,
    usedPercent,
    windowDurationMins: duration,
    resetsAtIso,
  };
}

function chooseSnapshot(rateLimits) {
  const byId = rateLimits?.rateLimitsByLimitId;
  return byId?.codex || rateLimits?.rateLimits || null;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function median(values) {
  const ordered = values
    .filter((value) => typeof value === "number" && Number.isFinite(value))
    .sort((left, right) => left - right);
  if (!ordered.length) return null;
  return ordered[Math.floor(ordered.length / 2)];
}

// Select one real provider snapshot rather than synthesizing fields from
// different responses. That keeps percentages and reset timestamps coherent.
function snapshotDistance(snapshot, primaryMedian, secondaryMedian) {
  let distance = 0;
  let compared = 0;
  for (const [window, target] of [
    [snapshot?.primary, primaryMedian],
    [snapshot?.secondary, secondaryMedian],
  ]) {
    if (target === null) continue;
    const value = safeNumber(window?.usedPercent);
    if (value === null) {
      distance += 200;
    } else {
      distance += Math.abs(value - target);
    }
    compared += 1;
  }
  return compared ? distance : 0;
}

function selectRateLimitConsensus(samples) {
  const available = samples
    .map((value, index) => ({ value, index, snapshot: chooseSnapshot(value) }))
    .filter((item) => item.snapshot);
  if (!available.length) {
    return {
      value: samples.at(-1) ?? null,
      diagnostics: { sampleCount: samples.length, usableSamples: 0 },
    };
  }

  // Readiness is safety-sensitive, so vote on the provider's reached flag
  // first. Within that majority, select the sample nearest the median usage;
  // this rejects both blank-window and stale-usage outliers.
  const blockedCount = available.filter(
    (item) => Boolean(item.snapshot.rateLimitReachedType),
  ).length;
  const majorityBlocked = blockedCount > available.length / 2;
  const candidates = available.filter(
    (item) => Boolean(item.snapshot.rateLimitReachedType) === majorityBlocked,
  );
  const primaryMedian = median(
    candidates.map((item) => safeNumber(item.snapshot.primary?.usedPercent)),
  );
  const secondaryMedian = median(
    candidates.map((item) => safeNumber(item.snapshot.secondary?.usedPercent)),
  );
  const selected = candidates.reduce((best, item) => {
    const distance = snapshotDistance(item.snapshot, primaryMedian, secondaryMedian);
    const newerTie = best && distance === best.distance && item.index > best.item.index;
    if (!best || distance < best.distance || newerTie) {
      return { item, distance };
    }
    return best;
  }, null).item;

  return {
    value: selected.value,
    diagnostics: {
      sampleCount: samples.length,
      usableSamples: available.length,
      blockedSamples: blockedCount,
      selectedBlocked: majorityBlocked,
      selectedIndex: selected.index,
      disagreement: blockedCount > 0 && blockedCount < available.length,
    },
  };
}

async function readRateLimitConsensus() {
  const samples = [];
  for (let index = 0; index < RATE_LIMIT_SAMPLE_COUNT; index += 1) {
    samples.push(await send("account/rateLimits/read"));
    if (index + 1 < RATE_LIMIT_SAMPLE_COUNT) {
      await sleep(RATE_LIMIT_SAMPLE_PAUSE_MS);
    }
  }
  return selectRateLimitConsensus(samples);
}

function normalizeRateLimits(rateLimits) {
  const snapshot = chooseSnapshot(rateLimits);
  const primary = normalizeWindow(snapshot?.primary, "Primary");
  const secondary = normalizeWindow(snapshot?.secondary, "Secondary");
  const windows = [primary, secondary].filter(Boolean);
  const shortWindow =
    windows.find((item) => item.windowDurationMins === 300) ||
    windows.find((item) => item.windowDurationMins !== null && item.windowDurationMins <= 360) ||
    primary ||
    null;
  const weeklyWindow =
    windows.find((item) => item.windowDurationMins === 10080) ||
    windows.find((item) => item.windowDurationMins !== null && item.windowDurationMins >= 7 * 24 * 60) ||
    secondary ||
    null;

  return {
    limitId: snapshot?.limitId ?? null,
    limitName: snapshot?.limitName ?? null,
    planType: snapshot?.planType ?? null,
    rateLimitReachedType: snapshot?.rateLimitReachedType ?? null,
    shortWindow,
    weeklyWindow,
    credits: snapshot?.credits ?? null,
    individualLimit: snapshot?.individualLimit ?? null,
    rateLimitResetCredits: rateLimits?.rateLimitResetCredits ?? null,
  };
}

function normalizeUsage(usage) {
  return {
    summary: usage?.summary ?? null,
    dailyUsageBuckets: Array.isArray(usage?.dailyUsageBuckets) ? usage.dailyUsageBuckets.slice(-14) : null,
  };
}

child.stdout.on("data", (chunk) => {
  buffer += chunk.toString("utf8");
  while (buffer.includes("\n")) {
    const index = buffer.indexOf("\n");
    const line = buffer.slice(0, index).trim();
    buffer = buffer.slice(index + 1);
    if (!line) continue;

    let message;
    try {
      message = JSON.parse(line);
    } catch {
      continue;
    }

    if (message.id !== undefined && pending.has(message.id)) {
      const request = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) request.reject(new Error(`${request.method}: ${message.error.message || JSON.stringify(message.error)}`));
      else request.resolve(message.result ?? message);
    }
  }
});

child.stderr.on("data", () => {
  // App-server may log warnings here. Avoid mixing logs into the JSON contract.
});

const timeout = setTimeout(() => {
  console.log(JSON.stringify({ ok: false, error: "Timed out waiting for Codex app-server." }));
  child.kill();
  process.exit(0);
}, 30000);

try {
  await send("initialize", {
    clientInfo: { name: "codex-account-launcher", title: "Codex Account Launcher", version: "0.2.0" },
    capabilities: null,
  });

  // A newly started app-server can briefly return a default, unused rate-limit
  // window before the selected CODEX_HOME account has finished loading. Force
  // account initialization before sampling limits; the samples below then
  // reject any remaining one-off placeholder response.
  await send("account/read", { refreshToken: false });

  let resetOutcome = null;
  if (action === "consume-reset") {
    const reset = await send("account/rateLimitResetCredit/consume", {
      idempotencyKey: randomUUID(),
    });
    resetOutcome = reset?.outcome ?? null;
    if (resetOutcome === "reset") await sleep(300);
  }

  let rateLimits = null;
  let rateLimitDiagnostics = null;
  let usage = null;
  let usageError = null;

  try {
    const consensus = await readRateLimitConsensus();
    rateLimits = consensus.value;
    rateLimitDiagnostics = consensus.diagnostics;
  } catch (error) {
    throw error;
  }

  try {
    usage = await send("account/usage/read");
  } catch (error) {
    usageError = error instanceof Error ? error.message : String(error);
  }

  clearTimeout(timeout);
  console.log(JSON.stringify({
    ok: true,
    refreshedAtIso: new Date().toISOString(),
    resetOutcome,
    rateLimits: normalizeRateLimits(rateLimits),
    rateLimitDiagnostics,
    usage: normalizeUsage(usage),
    usageError,
  }));
  child.kill();
} catch (error) {
  clearTimeout(timeout);
  console.log(JSON.stringify({ ok: false, error: error instanceof Error ? error.message : String(error) }));
  child.kill();
}
