import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
const systemChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
export default defineConfig({
  testDir: "tests/browser", workers: 1, timeout: 45000,
  use: { baseURL: "http://127.0.0.1:7871", permissions: ["microphone"],
    launchOptions: {executablePath: process.env.CHROME_PATH || (existsSync(systemChrome) ? systemChrome : undefined),
      args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"]}},
  webServer: { command: ".venv/bin/python -m uvicorn app:app --app-dir apps/demo/server --host 127.0.0.1 --port 7871",
    url: "http://127.0.0.1:7871/config", env: {JEV_DEMO_FAKE: "1"}, reuseExistingServer: false, timeout: 60000 }
});
