import { expect, test } from "vitest";
import { duration, milliseconds, responseDelay } from "../../../apps/demo/web/src/latency";
import type { TurnLatency } from "../src/index";

test("latency labels preserve zero and unknown measurements", () => {
  expect(milliseconds(0)).toBe("0 ms");
  expect(milliseconds(null)).toBe("n/a");
  expect(milliseconds(1234)).toBe("1.23 s");
  expect(duration({stage: "stt", start_ms: null, end_ms: 100, status: "complete"})).toBeNull();
  expect(duration({stage: "llm", start_ms: 100, end_ms: 180, status: "complete"})).toBe(80);
});

test("total is speech-end to first audio, not the sum of overlapping stages", () => {
  const turn = {speech_end_ms: 1000, first_audio_ms: 1800} as TurnLatency;
  expect(responseDelay(turn)).toBe(800);
  expect(responseDelay({...turn, speech_end_ms: null})).toBeNull();
});
