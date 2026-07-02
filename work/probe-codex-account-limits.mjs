import { spawn } from "node:child_process";

const codexPath = process.argv[2];
const codexHome = process.argv[3];
const workspace = process.argv[4] || process.cwd();

if (!codexPath || !codexHome) {
  console.error("Usage: node probe-codex-account-limits.mjs <codex.exe> <CODEX_HOME> [workspace]");
  process.exit(2);
}

const child = spawn(codexPath, ["app-server", "--stdio"], {
  cwd: workspace,
  env: { ...process.env, CODEX_HOME: codexHome },
  stdio: ["pipe", "pipe", "pipe"],
});

let buffer = "";
let nextId = 1;
const pending = new Map();

function send(method, params = undefined) {
  const id = nextId++;
  const request = { id, method, params };
  child.stdin.write(`${JSON.stringify(request)}\n`);
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject, method });
  });
}

child.stdout.on("data", (chunk) => {
  buffer += chunk.toString("utf8");
  while (buffer.includes("\n")) {
    const idx = buffer.indexOf("\n");
    const line = buffer.slice(0, idx).trim();
    buffer = buffer.slice(idx + 1);
    if (!line) continue;

    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      console.log(JSON.stringify({ raw: line }));
      continue;
    }

    if (msg.id !== undefined && pending.has(msg.id)) {
      const item = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) item.reject(new Error(`${item.method}: ${JSON.stringify(msg.error)}`));
      else item.resolve(msg.result ?? msg);
    }
  }
});

child.stderr.on("data", (chunk) => {
  const text = chunk.toString("utf8").trim();
  if (text) console.error(text);
});

const timeout = setTimeout(() => {
  console.error("Timed out waiting for app-server response.");
  child.kill();
  process.exit(1);
}, 20000);

try {
  await send("initialize", {
    clientInfo: { name: "codex-account-launcher-probe", title: "Codex Account Launcher Probe", version: "0.1.0" },
    capabilities: null,
  });

  const rateLimits = await send("account/rateLimits/read");
  const usage = await send("account/usage/read");
  clearTimeout(timeout);
  console.log(JSON.stringify({ rateLimits, usage }, null, 2));
  child.kill();
} catch (error) {
  clearTimeout(timeout);
  console.error(error instanceof Error ? error.message : String(error));
  child.kill();
  process.exit(1);
}
