// Local setup and demo launcher. Provider keys stay in the Python process.
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
const windows = process.platform === "win32";
const python = path.join(root, ".venv", windows ? "Scripts/python.exe" : "bin/python");
const npm = windows ? "npm.cmd" : "npm";
const action = process.argv[2];
function run(command, args, env = process.env) {
  const result = spawnSync(command, args, { cwd: root, stdio: "inherit", env, shell: windows && command === npm });
  if (result.error) console.error(result.error.message);
  if (result.status !== 0) process.exit(result.status ?? 1);
}
if (Number(process.versions.node.split(".")[0]) < 22) {
  console.error("Node.js 22+ is required."); process.exit(1);
}
if (action === "setup") {
  if (!existsSync(python)) {
    const candidates = process.env.PYTHON ? [process.env.PYTHON] : ["python3.12", "python3.13", "python3.11", "python3", "python"];
    const selected = candidates.find(command => spawnSync(command, ["-c", "import sys; sys.exit(not ((3,11) <= sys.version_info[:2] < (3,14)))"]).status === 0);
    if (!selected) { console.error("Install Python 3.12 (3.11–3.13 supported), then rerun npm run setup. Or set PYTHON to its executable."); process.exit(1); }
    run(selected, ["-m", "venv", ".venv"]);
  }
  run(python, ["-m", "pip", "install", "-e", "packages/python[webrtc,test,demo]"]);
  run(npm, ["ci"]);
  run(npm, ["run", "build"]);
  console.log("Ready. Copy .env.example to .env and add your keys, then npm run demo. No keys? npm run demo:offline.");
} else if (action === "demo" || action === "offline") {
  if (!existsSync(python)) { console.error("Run npm run setup first."); process.exit(1); }
  const port = process.env.PORT ?? "7870";
  if (!/^\d+$/.test(port) || +port < 1 || +port > 65535) { console.error("PORT must be 1–65535."); process.exit(1); }
  run(npm, ["run", "build"]);
  console.log(`Open http://127.0.0.1:${port}/ — ${action === "offline" ? "synthetic transport test; no provider calls" : "live providers; API charges apply"}`);
  run(python, ["-m", "uvicorn", "app:app", "--app-dir", "apps/demo/server", "--host", "127.0.0.1", "--port", port],
    { ...process.env, JEV_DEMO_FAKE: action === "offline" ? "1" : "0" });
} else {
  console.error("Use npm run setup, npm run demo, or npm run demo:offline."); process.exit(1);
}
