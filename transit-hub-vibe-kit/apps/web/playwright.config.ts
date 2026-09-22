import { defineConfig, devices } from "@playwright/test";

const browserChannel = process.env.PLAYWRIGHT_CHANNEL;
const chromeChannel = browserChannel ? { channel: browserChannel } : {};

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  reporter: process.env.CI ? "line" : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"], ...chromeChannel, timezoneId: "Asia/Shanghai" },
    },
    {
      name: "desktop-los-angeles",
      use: {
        ...devices["Desktop Chrome"],
        ...chromeChannel,
        timezoneId: "America/Los_Angeles",
      },
    },
    {
      name: "mobile-chromium",
      use: { ...devices["Pixel 7"], ...chromeChannel, timezoneId: "Asia/Shanghai" },
    },
  ],
  webServer: {
    command: "npm run dev -- --hostname 127.0.0.1 --port 3100",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
