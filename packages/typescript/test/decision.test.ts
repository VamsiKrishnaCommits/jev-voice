import { expect, test } from "vitest";
import { decisionView, emptyDecision } from "../../../apps/demo/web/src/decision";
import type { TurnDecision } from "../src/index";

const event = (extra: Partial<TurnDecision>): TurnDecision => ({ kind: "jev_response", strategy: "JevTurnTaking", turn: 2, revision: 4, seq: 10, ...extra });

test.each(["hold", "yield", "uncertain"] as const)("visible %s decision with latency", decision => {
  const view = decisionView(emptyDecision(), event({decision, latency_ms: 123}));
  expect(view.title).toBe(decision.toUpperCase());
  expect(view.tone).toBe(decision);
  expect(view.last).toContain("123 ms");
});

test("fallback does not impersonate Jev approval; latest verdict survives handoff", () => {
  const hold = decisionView(emptyDecision(), event({decision: "hold"}));
  const fallback = decisionView(hold, event({kind: "handoff", reason: "max_silence_timeout"}));
  expect(fallback.tone).toBe("fallback");
  expect(fallback.detail).toContain("not Jev approval");
  expect(fallback.last).toContain("HOLD");
  expect(decisionView(hold, event({kind: "handoff", reason: "uncertain_timeout"})).detail).toContain("not Jev approval");
  expect(decisionView(hold, event({kind: "handoff", reason: "yield"})).title).toBe("YIELD · handed off");
});

test("checking preserves last result; speech resets stale result", () => {
  const hold = decisionView(emptyDecision(), event({decision: "hold"}));
  const checking = decisionView(hold, event({kind: "jev_request"}));
  expect(checking.last).toBe(hold.last);
  const listening = decisionView(checking, event({kind: "speech_started"}));
  expect(listening.title).toBe("Listening");
  expect(listening.last).toBe("No decision yet");
});

test("silence baseline is never labeled Jev approval", () => {
  const view = decisionView(emptyDecision(), event({kind: "handoff", reason: "yield", strategy: "SilenceTurnTaking"}));
  expect(view.detail).toContain("Jev was not consulted");
});
