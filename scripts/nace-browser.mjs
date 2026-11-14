#!/usr/bin/env node
// Runs the Nace browser harness runner with Reach's keys. Loads the repo .env into this process only (never printed),
// maps NACE_API_KEY to the runner's DREX_API_KEY, then hands over to the runner CLI.
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
try { process.loadEnvFile(join(root, ".env")); } catch { /* keys may already be in the environment */ }
if (!process.env.DREX_API_KEY && process.env.NACE_API_KEY) process.env.DREX_API_KEY = process.env.NACE_API_KEY;

const harness = process.env.REACH_BROWSER_HARNESS_DIR || join(root, "vendor", "drex-browser-harness");
const child = spawn(process.execPath, [join(harness, "runner", "bin", "nace-browser.mjs"), ...process.argv.slice(2)], { stdio: "inherit", env: process.env });
child.on("exit", (code) => process.exit(code ?? 1));
