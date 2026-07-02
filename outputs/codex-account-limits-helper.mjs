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

  let resetOutcome = null;
  if (action === "consume-reset") {
    const reset = await send("account/rateLimitResetCredit/consume", {
      idempotencyKey: randomUUID(),
    });
    resetOutcome = reset?.outcome ?? null;
  }

  let rateLimits = null;
  let usage = null;
  let usageError = null;

  try {
    rateLimits = await send("account/rateLimits/read");
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
    usage: normalizeUsage(usage),
    usageError,
  }));
  child.kill();
} catch (error) {
  clearTimeout(timeout);
  console.log(JSON.stringify({ ok: false, error: error instanceof Error ? error.message : String(error) }));
  child.kill();
}
