import type { TurnDecision } from "@jev/voice";

export interface DecisionView { tone: string; title: string; detail: string; last: string }
export const emptyDecision = (): DecisionView => ({ tone: "idle", title: "Waiting for speech", detail: "Live turn decisions will appear here.", last: "No decision yet" });

export function decisionView(previous: DecisionView, event: TurnDecision): DecisionView {
  const next = { ...previous };
  const jev = event.strategy === "JevTurnTaking";
  const source = jev ? "Jev" : event.strategy === "LiveKitTurnTaking" ? "LiveKit" : "Turn strategy";
  if (event.kind === "speech_started") {
    return { ...emptyDecision(), title: "Listening", detail: "You’re speaking · any pending handoff is cancelled." };
  }
  if (event.kind.endsWith("_request")) {
    return { ...next, tone: "checking", title: `${source} is checking…`, detail: "Evaluating whether your thought is complete." };
  }
  if (event.kind.endsWith("_response") && ["hold", "yield", "uncertain"].includes(event.decision ?? "")) {
    const decision = event.decision!;
    return { tone: decision, title: decision.toUpperCase(),
      detail: ({hold: "Not finished yet · keeping the floor yours.", yield: "A natural response point · ready to hand off when you stop.", uncertain: "Not sure yet · allowing a short pause."})[decision],
      last: `Turn ${event.turn} · ${source}: ${decision.toUpperCase()}${typeof event.latency_ms === "number" ? ` · ${Math.round(event.latency_ms)} ms` : ""}` };
  }
  if (event.kind.endsWith("_error")) {
    return { ...next, tone: "fallback", title: `${source} unavailable`, detail: "The bounded silence fallback is still active." };
  }
  if (event.kind === "handoff") {
    const approved = event.reason === "yield" && jev;
    const baseline = event.strategy === "SilenceTurnTaking";
    return { ...next, tone: approved ? "yield" : "fallback",
      title: approved ? "YIELD · handed off" : baseline ? "Silence timeout · handed off" : "Handed off",
      detail: approved ? "Jev approved the response. The assistant can answer now."
        : event.reason === "max_silence_timeout" ? "Maximum silence reached · controller fallback, not Jev approval."
        : event.reason === "uncertain_timeout" ? "Uncertainty pause elapsed · controller fallback, not Jev approval."
        : baseline ? "Fixed silence baseline · Jev was not consulted."
        : `Turn released · ${event.reason?.replaceAll("_", " ") ?? "controller handoff"}.` };
  }
  return previous;
}
