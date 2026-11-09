import { test, expect } from "@playwright/test";

test("missing credentials explain setup and allow switching to a ready mode", async ({page}) => {
  await page.route("**/config", route => route.fulfill({json: {
    modes: ["jev", "silence"], fake: false,
    setup: {jev: {ready: false, missing: ["TYPESAFE_API_KEY"]}, silence: {ready: true, missing: []}},
  }}));
  await page.goto("/");
  await expect(page.locator("#setup-notice")).toContainText("TYPESAFE_API_KEY");
  await expect(page.locator("#connect")).toBeDisabled();
  await expect(page.locator("#livekit")).toBeHidden();
  await page.locator("#silence").click();
  await expect(page.locator("#connect")).toBeEnabled();
  await expect(page.locator("#setup-notice")).toBeHidden();
});

test("configuration failure prevents starting a broken session", async ({page}) => {
  await page.route("**/config", route => route.fulfill({status: 503, body: "unavailable"}));
  await page.goto("/");
  await expect(page.locator("#error")).toContainText("Could not load server configuration");
  await expect(page.locator("#connect")).toBeDisabled();
});

test("both SDKs connect, exchange audio/transcripts, switch mode and close", async ({page}) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator(".local")).toContainText("OFFLINE TEST · SYNTHETIC PROVIDERS");
  await page.getByRole("button", {name: "Start a conversation"}).click();
  await expect(page.locator("#connection")).toHaveText("connected", {timeout: 25000});
  await expect(page.locator("#transcript")).toContainText("Synthetic response", {timeout: 15000});
  // A real remote audio track, not just a successful HTTP handshake.
  await expect.poll(() => page.evaluate(() => {
    const audio = document.querySelector("audio");
    return audio?.currentTime ?? 0;
  })).toBeGreaterThan(0);
  await expect(page.locator("#counts")).toContainText("1 handoffs");
  await expect(page.locator("#decision-title")).toHaveText("Silence timeout · handed off");
  await expect(page.locator("#decision-detail")).toContainText("Jev was not consulted");
  await expect(page.locator(".latency-turn")).toContainText("Turn 1");
  await expect(page.locator('.latency-row[data-stage="llm"] .latency-bar.complete')).toHaveCount(1);
  await expect(page.locator('.latency-row[data-stage="tts"] .latency-bar.complete')).toHaveCount(1);
  await page.screenshot({path: "test-results/demo-desktop.png", fullPage: true});
  await page.getByRole("button", {name: "Mute", exact: true}).click();
  await expect(page.getByRole("button", {name: "Unmute", exact: true})).toBeVisible();
  await page.getByRole("button", {name: "End conversation"}).click();
  await expect(page.locator("#connection")).toHaveText("disconnected");
  await page.locator("#silence").click();
  await expect(page.locator("#decision-title")).toHaveText("Waiting for speech");
  await page.locator(".trace > details > summary").click();
  await page.locator("#debug").uncheck();
  await page.getByRole("button", {name: "Start a conversation"}).click();
  await expect(page.locator("#connection")).toHaveText("connected", {timeout: 25000});
  await expect(page.locator("#transcript")).toContainText("Synthetic response", {timeout: 15000});
  await expect(page.locator("#decision-title")).toHaveText("Silence timeout · handed off");
  await expect(page.locator('.latency-row[data-stage="tts"] .latency-bar.complete')).toHaveCount(1);
  await page.getByRole("button", {name: "End conversation"}).click();
  await expect(page.locator("#connection")).toHaveText("disconnected");
  expect(errors).toEqual([]);
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: "test-results/demo-mobile.png", fullPage: true});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
