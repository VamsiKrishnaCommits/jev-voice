import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { VoiceClient } from "../src/index.js";

let stopped: ReturnType<typeof vi.fn>;
const audio = () => ({ autoplay: false, hidden: false, srcObject: null, play: vi.fn(async()=>{}), pause: vi.fn(), remove: vi.fn() });
beforeEach(() => {
  stopped = vi.fn();
  vi.stubGlobal("document", { createElement: audio, body: { append: vi.fn() } });
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn(async () => ({getTracks: () => [{stop: stopped}], getAudioTracks: () => []})) } });
});
afterEach(() => { vi.unstubAllGlobals(); });

test("live decisions dispatch without debug polling", async () => {
  const c = new VoiceClient({endpoint: "/voice", debug: false});
  const callback = vi.fn(); c.on("turn_decision", callback);
  const message = {type: "turn_decision", kind: "jev_response", strategy: "JevTurnTaking", turn: 1, revision: 2, seq: 3, decision: "hold", latency_ms: 100};
  // Exercise the data-channel message dispatcher, without a provider call.
  (c as any).receive(message);
  expect(callback).toHaveBeenCalledWith(message);
  (c as any).receive({type: "turn_decision", kind: "jev_response"});
  expect(callback).toHaveBeenCalledTimes(1);
  const latency = vi.fn(); c.on("turn_latency", latency);
  const sample = {type: "turn_latency", turn: 1, seq: 1, elapsed_ms: 500, spans: []};
  (c as any).receive(sample);
  expect(latency).toHaveBeenCalledWith(sample);
  await c.dispose();
});

test("callbacks unsubscribe and internal decisions are not required", async () => {
  const c = new VoiceClient({endpoint: "/voice"});
  const callback = vi.fn();
  const off = c.on("state", callback);
  await c.disconnect();
  expect(callback).toHaveBeenCalledWith({state: "disconnected"});
  off(); await c.disconnect();
  expect(callback).toHaveBeenCalledTimes(1);
  await c.dispose();
});

test("failed signaling stops microphone and resets state", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("failure", {status: 503})));
  const c = new VoiceClient({endpoint: "/voice"});
  const errors = vi.fn(); c.on("error", errors);
  await expect(c.connect()).rejects.toThrow("503");
  expect(stopped).toHaveBeenCalledOnce();
  expect(c.state).toBe("disconnected");
  expect(errors).toHaveBeenCalledOnce();
  await c.dispose();
});

test("disconnect while permission is pending stops late microphone", async () => {
  let resolve!: (v: any) => void;
  vi.stubGlobal("navigator", {mediaDevices: {getUserMedia: () => new Promise(r => {resolve=r})}});
  const c = new VoiceClient({endpoint: "/voice"});
  const connect = c.connect();
  await c.disconnect();
  resolve({ getTracks: () => [{stop: stopped}] });
  await connect;
  expect(stopped).toHaveBeenCalledOnce();
  expect(c.state).toBe("disconnected");
  await c.dispose();
});
