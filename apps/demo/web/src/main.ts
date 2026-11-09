import { VoiceClient } from "@jev/voice";
import "./style.css";
import "./layout.css";
import { decisionView, emptyDecision, type DecisionView } from "./decision";
import { LatencyChart } from "./latency";
import { view } from "./view";

document.querySelector<HTMLDivElement>("#app")!.innerHTML = view;

const latencySection = document.createElement("section");
latencySection.className = "latency-section";
latencySection.setAttribute("aria-label", "Per-turn latency waterfall");
latencySection.innerHTML = `<h2>Where the time goes.</h2><p class="latency-help">Each turn, from speech to the first audio frame. Bars share a time axis; overlapping work is not added together.</p><div id="latency-turns"></div><details class="latency-definitions"><summary>What these timings mean</summary><p><b>STT:</b> detected speech start → first transcript (includes capturing speech; not provider compute time). <b>Jev:</b> individual decision requests, including rechecks; striped bars were cancelled. <b>LLM:</b> context received → first text. <b>TTS:</b> first LLM text → first server audio, including text buffering. The headline measures the last speech-stop signal → first server audio, not audible browser playback. Missing measurements show n/a or not reached. These are live observations, not a benchmark.</p></details>`;
document.querySelector(".lower")!.before(latencySection);

const el = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const latencyChart = new LatencyChart(el("latency-turns"));
latencyChart.reset();
let mode = "jev", client: VoiceClient | undefined, connected = false, busy = false;
let partialUser: HTMLElement | undefined, assistantText: HTMLElement | undefined;
let holds = 0, handoffs = 0;
const modes = ["jev", "silence", "livekit"];
let availableModes = ["jev", "silence"];
let configurationLoaded = false;
let setup: Record<string, { ready: boolean; missing: string[] }> = {};
function refreshControls() {
  const missing = setup[mode]?.missing ?? [];
  el<HTMLButtonElement>("connect").disabled = busy || (!connected && (!configurationLoaded || missing.length > 0));
  el("setup-notice").hidden = missing.length === 0;
  el("setup-notice").textContent = missing.length ? `Add ${missing.join(", ")} to your server .env, then restart. For a no-key transport preview, run npm run demo:offline.` : "";
  for (const v of modes) el<HTMLButtonElement>(v).disabled = busy || !availableModes.includes(v);
}
let decision = emptyDecision();
function showDecision(next: DecisionView) {
  decision = next;
  el("decision-card").dataset.tone = next.tone;
  el("decision-title").textContent = next.title;
  el("decision-detail").textContent = next.detail;
  el("decision-last").textContent = next.last;
}
function resetDecision() {
  el("decision-source").textContent = mode === "jev" ? "JEV · LIVE DECISION" : mode === "livekit" ? "LIVEKIT · LIVE DECISION" : "SILENCE BASELINE · NO JEV";
  showDecision(emptyDecision());
}
function bubble(speaker: string) {
  el("transcript").querySelector(".empty")?.remove();
  const p = document.createElement("p"); p.className = "bubble " + speaker;
  const label = document.createElement("small"); label.textContent = speaker === "user" ? "YOU" : "ASSISTANT";
  const span = document.createElement("span"); p.append(label, span); el("transcript").append(p);
  return span;
}
function activity(value: string) {
  el("orb").dataset.activity = value;
  el("activity").textContent = ({ listening: "I’m listening.", thinking: "A thought is taking shape…", speaking: "Let’s talk.", hold: "A moment to think…", idle: "Your turn." } as Record<string,string>)[value] ?? value;
}
for (const value of modes) el<HTMLButtonElement>(value).onclick = async () => {
  if (busy) return;
  await client?.dispose(); client = undefined; connected = false; mode = value;
  for (const v of modes) { el(v).classList.toggle("selected", v === mode); el(v).setAttribute("aria-pressed", String(v === mode)); }
  el("mode-note").textContent = mode === "livekit" ? "Real hosted v1 audio predictions · 0.3–2.5s endpointing · Pipecat adapter, not the full LiveKit stack. Audio is sent to LiveKit. No mini fallback." : mode === "silence" ? "Fixed 0.8-second silence baseline. No semantic model." : "Natural pauses · 1.5s uncertainty budget · 4s maximum silence after speech stops. No timed wait commands.";
  activity("idle");
  resetDecision();
  latencyChart.reset();
  refreshControls();
};
el<HTMLButtonElement>("connect").onclick = async () => {
  if (connected) { await client?.disconnect(); return; }
  if (busy || !configurationLoaded || (setup[mode]?.missing.length ?? 0) > 0) return;
  busy = true; el("error").textContent = "";
  el<HTMLButtonElement>("connect").disabled = true;
  for (const v of modes) el<HTMLButtonElement>(v).disabled = true;
  el("transcript").replaceChildren(); el("trace").replaceChildren(); partialUser = assistantText = undefined;
  holds = handoffs = 0;
  el("counts").textContent = "0 holds · 0 handoffs";
  el("mute").textContent = "Mute";
  el("audio").hidden = true;
  await client?.dispose();
  resetDecision();
  client = new VoiceClient({ endpoint: "/voice", agent: mode, debug: el<HTMLInputElement>("debug").checked });
  latencyChart.reset();
  client.on("turn_latency", event => latencyChart.update(event));
  client.on("state", ({ state }) => {
    connected = state === "connected";
    el("connection").textContent = state;
    el("connect").textContent = connected ? "End conversation" : "Start a conversation ↗";
    el("mute").hidden = !connected;
    refreshControls();
    if (state === "disconnected") {
      activity("idle");
      latencyChart.close();
      showDecision({ ...decision, tone: "idle", title: "Conversation ended", detail: "Start a new conversation to see live decisions." });
    }
  });
  client.on("turn_decision", event => {
    el("decision-source").textContent = event.strategy === "JevTurnTaking" ? "JEV · LIVE DECISION" : event.strategy === "LiveKitTurnTaking" ? "LIVEKIT · LIVE DECISION" : "SILENCE / CUSTOM STRATEGY · NO JEV";
    showDecision(decisionView(decision, event));
  });
  client.on("activity", event => activity(event.activity));
  client.on("error", event => { el("error").textContent = event.message; if (event.code === "playback_blocked") el("audio").hidden = false; });
  client.on("response_start", () => { assistantText = undefined; });
  client.on("response_end", () => { assistantText = undefined; });
  client.on("interruption", () => { assistantText = undefined; });
  client.on("transcript", event => {
    if (event.speaker === "user") {
      partialUser ??= bubble("user"); partialUser.textContent = event.text;
      if (event.final) partialUser = undefined;
    } else {
      assistantText ??= bubble("assistant");
      assistantText.textContent = event.delta ? assistantText.textContent + event.text : event.text;
    }
    el("transcript").scrollTop = el("transcript").scrollHeight;
  });
  client.on("debug", event => {
    if (event.kind === "hold") holds++;
    if (event.kind === "livekit_error") el("error").textContent = String(event.message);
    if (event.kind === "handoff") handoffs++;
    el("counts").textContent = `${holds} holds · ${handoffs} handoffs`;
    if (!["jev_request", "jev_response", "livekit_request", "livekit_response", "livekit_error", "hold", "handoff", "silence_limit", "jev_error"].includes(String(event.kind))) return;
    const row = document.createElement("details"); row.className = event.kind === "handoff" ? "handoff" : "";
    const title = document.createElement("summary");
    const kind = String(event.kind).replace("jev_", mode === "jev" ? "Jev " : "Silence ").replace("livekit_", "LiveKit ");
    title.textContent = `${(Number(event.elapsedMs)/1000).toFixed(1)}s · ${kind} ${event.decision ?? event.reason ?? ""}${event.latency_ms ? ` · ${event.latency_ms}ms` : ""}`;
    const pre = document.createElement("pre"); pre.textContent = JSON.stringify(event, null, 2);
    row.append(title, pre); el("trace").prepend(row);
    if (el("trace").children.length > 150) el("trace").lastElementChild?.remove();
  });
  try { await client.connect(); } catch { /* Error callback gives actionable detail. */ }
  finally { busy = false; refreshControls(); }
};
el<HTMLButtonElement>("mute").onclick = () => { if (client) { client.setMuted(!client.muted); el("mute").textContent = client.muted ? "Unmute" : "Mute"; } };
el<HTMLButtonElement>("audio").onclick = async () => { await client?.enableAudio(); el("audio").hidden = true; };
window.addEventListener("pagehide", () => { void client?.disconnect(); });
fetch("/config").then(r => { if (!r.ok) throw new Error("Configuration unavailable"); return r.json(); }).then(config => {
  el("voice-model").textContent = config.fake ? "Synthetic test audio · no paid TTS" : `${config.llm_model ?? "Unknown LLM"} · ${config.tts_model ?? "Unknown TTS"}${config.expressive_voice ? " · expressive audio tags enabled" : " · plain speech"}`;
  availableModes = config.modes ?? ["jev", "silence"];
  setup = config.setup ?? {};
  configurationLoaded = true;
  el("livekit").hidden = !availableModes.includes("livekit");
  refreshControls();
  if (config.fake) { document.querySelector(".local")!.textContent = "OFFLINE TEST · SYNTHETIC PROVIDERS"; document.title = "OFFLINE TEST — Jev"; }
}).catch(() => { el("error").textContent = "Could not load server configuration. Start the server with npm run demo, then refresh."; });
