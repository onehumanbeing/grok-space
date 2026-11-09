#!/usr/bin/env node
// Public, relocatable runner. Own Chromium contexts, ephemeral ports and test stores.
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { readFile, mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { once } from "node:events";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const python = process.env.UI_TEST_PYTHON || (process.platform === "darwin" ? "/usr/bin/python3" : "python3");
const selected = process.argv.find((value) => value.startsWith("--suite="))?.split("=")[1] || "all";
if (!["all", "foundation", "shell", "worker", "preview"].includes(selected)) throw new Error("Use --suite=all|foundation|shell|worker|preview");
const reports = [];
const children = new Set();
let browser;
await mkdir(path.join(root, "out"), { recursive: true });
await mkdir(path.join(root, "output/playwright"), { recursive: true });

function child(args, extra = {}) {
  const processChild = spawn(python, args, { cwd: root, stdio: ["ignore", "pipe", "pipe"], ...extra });
  children.add(processChild);
  processChild.once("exit", () => children.delete(processChild));
  return processChild;
}
async function stop(processChild) {
  if (processChild.exitCode !== null || processChild.signalCode !== null) return;
  processChild.kill("SIGINT");
  let timer;
  try { await Promise.race([once(processChild, "exit"), new Promise((resolve) => { timer = setTimeout(resolve, 3000); })]); }
  finally { clearTimeout(timer); }
  if (processChild.exitCode === null && processChild.signalCode === null) processChild.kill("SIGTERM");
}
async function command(args, extra = {}) {
  const processChild = child(args, extra);
  let output = "", errors = "";
  processChild.stdout.on("data", (data) => { output += data; });
  processChild.stderr.on("data", (data) => { errors += data; });
  const [code] = await once(processChild, "exit");
  if (code !== 0) throw new Error("Owned test command failed: " + errors.slice(-2000));
  return output.trim() ? JSON.parse(output.trim()) : null;
}
async function server(args) {
  const processChild = child(args);
  let output = "", errors = "";
  const origin = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Owned test service startup timed out")), 10000);
    processChild.stderr.on("data", (data) => { errors += data; });
    processChild.stdout.on("data", (data) => {
      output += data;
      const found = output.match(/http:\/\/127\.0\.0\.1:\d+|127\.0\.0\.1:\d+/);
      if (found) { clearTimeout(timer); resolve(found[0].startsWith("http") ? found[0] : "http://" + found[0]); }
    });
    processChild.once("error", reject);
    processChild.once("exit", (code) => { clearTimeout(timer); reject(new Error("Test service exited " + code + ": " + errors.slice(-2000))); });
  });
  return { processChild, origin };
}
async function run(file, page) {
  const source = await readFile(path.join(root, "tests", file), "utf8");
  // These are trusted repository test functions, never page-supplied code.
  const result = await (0, eval)("(" + source + "\n)")(page);
  reports.push({ file, result });
  console.log(JSON.stringify({ file, result }));
  return result;
}
async function withHost(prefix, setup, test) {
  const dataDir = await mkdtemp(path.join(root, "out", prefix));
  if (setup) await setup(dataDir);
  const owned = await server(["bridge/server.py", "--port", "0", "--data-dir", dataDir]);
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 } });
  const page = await context.newPage();
  globalThis.UI_TEST = { root, dataDir, origin: owned.origin };
  try { await page.goto(owned.origin); await test(page, dataDir, owned.origin); }
  finally { await context.close(); await stop(owned.processChild); }
}
try {
  browser = await chromium.launch({ headless: !process.argv.includes("--headed") });
  if (["all", "shell"].includes(selected)) await withHost("redesign-test-", null, async (page) => {
    await run("ui_redesign.js", page);
    await run("ui_avatar.js", page);
    await run("ui_drawer.js", page);
  });
  if (["all", "worker"].includes(selected)) await withHost("worker-ui-test-", (dataDir) => command(["tests/ui_worker_bootstrap.py", path.basename(dataDir)]), async (page) => {
    const result = await run("ui_worker.js", page);
    const detail = await (await page.request.get(globalThis.UI_TEST.origin + "/api/spaces")).json();
    const counts = await readFile(path.join(globalThis.UI_TEST.dataDir, "projects", detail.spaces[0].id, "ui-executions.jsonl"), "utf8");
    if (counts.trim().split("\n").length !== 1 || !counts.includes(result.event_id)) throw new Error("Worker executed more than once.");
  });
  if (["all", "foundation"].includes(selected)) await withHost("ui-test-", null, async (page, dataDir, origin) => {
    await run("ui_bootstrap.js", page);
    const driver = child(["tests/ui_completion_driver.py"], { env: { ...process.env, GROK_SPACE_UI_DATA_DIR: dataDir, GROK_SPACE_UI_ORIGIN: origin } });
    let driverErrors = "";
    driver.stderr.on("data", (data) => { driverErrors += data; });
    const driverDone = once(driver, "exit");
    try {
      await run("ui_foundation.js", page);
      const [code] = await driverDone;
      if (code !== 0) throw new Error("CLI completion driver failed: " + driverErrors);
      await run("ui_finish.js", page);
      await run("ui_revision_regression.js", page);
      const action = await run("ui_performance.js", page);
      await command(["bridge/cli.py", "--data-dir", dataDir, "action", "complete", "--space", action.spaceId, "--event", action.eventId, "--expected-revision", String(action.revision), "--json", JSON.stringify({ result: { conditionalProbe: true } })]);
      await run("ui_performance_result.js", page);
    } finally { await stop(driver); }
  });
  if (["all", "preview"].includes(selected)) {
    const owned = await server(["bridge/preview.py", "--html", "examples/minimal/index.html", "--state", "examples/minimal/state.json"]);
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    globalThis.UI_TEST = { root, origin: owned.origin, preview: true };
    try { await page.goto(owned.origin); await run("ui_preview.js", page); }
    finally { await context.close(); await stop(owned.processChild); }
  }
  await writeFile(path.join(root, "out/ui-results.json"), JSON.stringify(reports, null, 2) + "\n");
} finally {
  await browser?.close();
  await Promise.all([...children].map(stop));
  delete globalThis.UI_TEST;
}
