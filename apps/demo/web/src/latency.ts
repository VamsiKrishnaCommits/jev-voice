import type { LatencySpan, TurnLatency } from "@jev/voice";

export const milliseconds = (value: number | null) => value === null ? "n/a" : value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(2)} s`;
export function duration(span: LatencySpan): number | null {
  return span.start_ms === null || span.end_ms === null ? null : Math.max(0, span.end_ms - span.start_ms);
}
export function responseDelay(turn: TurnLatency): number | null {
  return turn.speech_end_ms === null || turn.first_audio_ms === null ? null : Math.max(0, turn.first_audio_ms - turn.speech_end_ms);
}

export class LatencyChart {
  private turns = new Map<number, { data: TurnLatency; node: HTMLDetailsElement }>();
  constructor(private container: HTMLElement) {}
  reset() {
    this.turns.clear();
    this.container.replaceChildren();
    const empty = document.createElement("p"); empty.className = "empty";
    empty.textContent = "Speak to see the measured pipeline for each turn.";
    this.container.append(empty);
  }
  close() {
    for (const { data } of this.turns.values()) {
      if (data.status === "listening" || data.status === "responding") this.update({ ...data, status: "closed", seq: data.seq + 1 });
    }
  }
  update(data: TurnLatency) {
    let item = this.turns.get(data.turn);
    if (item && item.data.seq > data.seq) return;
    if (!item) {
      this.container.querySelector(".empty")?.remove();
      for (const previous of this.turns.values()) previous.node.open = false;
      const node = document.createElement("details"); node.className = "latency-turn"; node.open = true;
      item = { data, node }; this.turns.set(data.turn, item); this.container.prepend(node);
    }
    item.data = data;
    const node = item.node; node.replaceChildren();
    const summary = document.createElement("summary");
    const delay = responseDelay(data);
    summary.textContent = `Turn ${data.turn} · ${delay === null ? data.status.replaceAll("_", " ") : `${milliseconds(delay)} to first audio`}${delay !== null && data.status === "interrupted" ? " · interrupted" : ""}`;
    node.append(summary);
    const extent = Math.max(1, data.first_audio_ms ?? data.elapsed_ms, ...data.spans.map(s => s.end_ms ?? s.start_ms ?? 0));
    const stages = ["stt", "jev", "llm", "tts"] as const;
    for (const stage of stages) {
      const spans = data.spans.filter(s => s.stage === stage);
      const row = document.createElement("div"); row.className = "latency-row"; row.dataset.stage = stage;
      const label = document.createElement("strong");
      label.textContent = stage === "jev" ? data.strategy === "LiveKitTurnTaking" ? "LiveKit" : data.strategy === "SilenceTurnTaking" ? "Gate" : "Jev" : stage.toUpperCase();
      const track = document.createElement("div"); track.className = "latency-track";
      if (data.speech_end_ms !== null) {
        const marker = document.createElement("i"); marker.className = "speech-marker";
        marker.style.left = `${Math.min(100, data.speech_end_ms / extent * 100)}%`;
        marker.title = `Speech stopped: ${milliseconds(data.speech_end_ms)}`; track.append(marker);
      }
      for (const span of spans) {
        if (span.start_ms === null) continue;
        const bar = document.createElement("span"); bar.className = `latency-bar ${span.status}`;
        bar.style.left = `${span.start_ms / extent * 100}%`;
        bar.style.width = `${Math.max(0, ((span.end_ms ?? extent) - span.start_ms) / extent * 100)}%`;
        const description = `${label.textContent}: ${milliseconds(span.start_ms)}–${milliseconds(span.end_ms)}, ${span.status}${span.decision ? ` · ${span.decision}` : ""}`;
        bar.title = description; bar.setAttribute("role", "img"); bar.setAttribute("aria-label", description);
        track.append(bar);
      }
      const value = document.createElement("span"); value.className = "latency-value";
      const last = spans.at(-1);
      value.textContent = !last ? stage === "jev" && data.strategy === "SilenceTurnTaking" ? "not used" : "not reached"
        : last.status === "pending" ? data.status === "closed" ? "stopped" : "pending…"
        : `${milliseconds(duration(last))}${last.status === "cancelled" ? " (cancelled)" : last.status === "error" ? " (error)" : ""}`;
      if (stage === "jev" && spans.length > 1) value.textContent = `${spans.length} checks · last ${value.textContent}`;
      row.append(label, track, value); node.append(row);
    }
    const axis = document.createElement("p"); axis.className = "latency-axis";
    axis.textContent = `0 → ${milliseconds(extent)} from first observed turn event · dotted line: speech stopped`;
    const note = document.createElement("p"); note.className = "latency-note";
    const handoff = data.handoff_ms !== null && data.speech_end_ms !== null ? milliseconds(Math.max(0, data.handoff_ms - data.speech_end_ms)) : "n/a";
    note.textContent = `Speech stop → handoff: ${handoff}${data.reason ? ` · ${data.reason.replaceAll("_", " ")}` : ""}.`;
    node.append(axis, note);
  }
}
