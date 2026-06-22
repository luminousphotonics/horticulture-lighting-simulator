// @ts-check

const { defineConfig, devices } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "tests/browser",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:5001",
    channel: "chrome",
    trace: "on-first-retry",
  },
  webServer: {
    command: "bash scripts/dev/run_web_smoke_server.sh",
    url: "http://127.0.0.1:5001/health",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile",
      use: { ...devices["Pixel 5"] },
    },
  ],
});
