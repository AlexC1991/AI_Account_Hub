import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import {
  normalizeRateLimits,
  selectRateLimitConsensus,
} from "./codex-rate-limit-normalizer.mjs";

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

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
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
