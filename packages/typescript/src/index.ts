export type State = "disconnected" | "connecting" | "connected";
export interface Transcript { speaker: "user" | "assistant"; text: string; final: boolean; delta?: boolean }
export interface VoiceError { code: string; message: string; recoverable: boolean }
export interface TurnDecision {
  kind: string;
  strategy: string;
  turn: number;
  revision: number;
  seq: number;
  decision?: "hold" | "yield" | "uncertain";
  latency_ms?: number;
  reason?: string;
  silence_ms?: number;
}
export interface LatencySpan {
  stage: "stt" | "jev" | "llm" | "tts";
  start_ms: number | null;
  end_ms: number | null;
  status: "pending" | "complete" | "cancelled" | "error";
  decision?: string;
}
export interface TurnLatency {
  turn: number;
  seq: number;
  strategy: string;
  status: "listening" | "responding" | "audio_ready" | "interrupted" | "error" | "closed";
  elapsed_ms: number;
  speech_end_ms: number | null;
  handoff_ms: number | null;
  first_audio_ms: number | null;
  reason?: string;
  spans: LatencySpan[];
}
export interface Events {
  state: { state: State };
  activity: { activity: "listening" | "thinking" | "speaking" };
  transcript: Transcript;
  interruption: Record<string, never>;
  response_start: Record<string, never>;
  response_end: Record<string, never>;
  error: VoiceError;
  debug: Record<string, unknown>;
  turn_decision: TurnDecision;
  turn_latency: TurnLatency;
}
export interface VoiceClientOptions {
  endpoint: string;
  agent?: string;
  debug?: boolean;
  getToken?: () => Promise<string>;
  audioElement?: HTMLAudioElement;
}
interface Session { session_id: string; token: string; ice_servers: RTCIceServer[]; protocol_version: number }

/** Native WebRTC audio client. No provider keys or Pipecat frames in this API. */
export class VoiceClient {
  private listeners = new Map<keyof Events, Set<(value: any) => void>>();
  private pc?: RTCPeerConnection;
  private microphone?: MediaStream;
  private session?: Session;
  private abort?: AbortController;
  private heartbeat?: ReturnType<typeof setInterval>;
  private generation = 0;
  private audio: HTMLAudioElement;
  private ownsAudio: boolean;
  state: State = "disconnected";
  muted = false;

  constructor(private options: VoiceClientOptions) {
    this.audio = options.audioElement ?? document.createElement("audio");
    this.ownsAudio = !options.audioElement;
    this.audio.autoplay = true;
    if (this.ownsAudio) { this.audio.hidden = true; document.body.append(this.audio); }
  }

  on<K extends keyof Events>(name: K, callback: (value: Events[K]) => void): () => void {
    let set = this.listeners.get(name);
    if (!set) { set = new Set(); this.listeners.set(name, set); }
    set.add(callback);
    return () => set!.delete(callback);
  }

  private emit<K extends keyof Events>(name: K, value: Events[K]): void {
    for (const callback of this.listeners.get(name) ?? []) {
      try { callback(value); } catch (error) { console.error("Voice event listener failed", error); }
    }
  }
  private setState(state: State) { this.state = state; this.emit("state", { state }); }
  private url(path: string) { return this.options.endpoint.replace(/\/$/, "") + path; }

  private async json(path: string, init: RequestInit = {}, token?: string) {
    const response = await fetch(this.url(path), { ...init,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers } });
    if (!response.ok) throw new Error(`Voice API returned HTTP ${response.status}`);
    return response.json();
  }

  /** Invoke from a click/tap. Captures microphone; rejects on denial or timeout. */
  async connect(): Promise<void> {
    if (this.state !== "disconnected") throw new Error("Already connected or connecting");
    const generation = ++this.generation;
    const abort = this.abort = new AbortController();
    const current = () => generation === this.generation && !abort.signal.aborted;
    this.setState("connecting");
    try {
      const mic = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
      if (!current()) { mic.getTracks().forEach(t => t.stop()); return; }
      this.microphone = mic;
      const token = await this.options.getToken?.();
      if (!current()) return;
      const session: Session = await this.json("/sessions", {
        method: "POST", signal: abort.signal,
        body: JSON.stringify({ agent: this.options.agent ?? "jev", debug: this.options.debug ?? false }) }, token);
      if (!current()) {
        await this.json(`/sessions/${session.session_id}`, { method: "DELETE" }, session.token);
        return;
      }
      this.session = session;
      if (session.protocol_version !== 1) throw new Error("Unsupported voice protocol");
      const pc = this.pc = new RTCPeerConnection({ iceServers: session.ice_servers });
      for (const track of mic.getTracks()) pc.addTrack(track, mic);
      this.setMuted(false);
      pc.ontrack = event => {
        if (!current()) return;
        this.audio.srcObject = event.streams[0] ?? new MediaStream([event.track]);
        void this.audio.play().catch(() => this.emit("error", {
          code: "playback_blocked", message: "Click Enable audio to allow playback.", recoverable: true }));
      };
      const channel = pc.createDataChannel("jev-events");
      channel.onmessage = event => {
        if (!current()) return;
        try { this.receive(JSON.parse(event.data)); } catch { /* Ignore non-JSON transport heartbeats. */ }
      };
      channel.onopen = () => {
        if (!current()) return;
        // SmallWebRTC uses these to detect closed/suspended clients.
        this.heartbeat = setInterval(() => { if (channel.readyState === "open") channel.send("ping"); }, 1000);
      };
      pc.onconnectionstatechange = () => {
        if (!current()) return;
        if (pc.connectionState === "connected") this.setState("connected");
        if (["failed", "closed", "disconnected"].includes(pc.connectionState)) {
          this.emit("error", { code: "connection_closed", message: "Connection ended. Reconnect to start a new conversation.", recoverable: true });
          void this.disconnect();
        }
      };
      await pc.setLocalDescription(await pc.createOffer());
      // Non-trickle ICE: all candidates go in the offer; no PATCH endpoint needed.
      await this.waitFor(() => pc.iceGatheringState === "complete", abort.signal, 12000);
      const answer = await this.json(`/sessions/${session.session_id}/offer`, {
        method: "POST", signal: abort.signal, body: JSON.stringify(pc.localDescription) }, session.token);
      if (!current()) return;
      await pc.setRemoteDescription(answer);
      await this.waitFor(() => pc.connectionState === "connected", abort.signal, 20000);
      if (this.options.debug && current()) void this.pollDebug(session, abort.signal);
    } catch (error) {
      if (!current()) return;
      this.emit("error", { code: "connect_failed", message: error instanceof Error ? error.message : "Connection failed", recoverable: true });
      await this.disconnect();
      throw error;
    }
  }

  private receive(event: Record<string, unknown>) {
    const type = event.type;
    if (type === "activity" && ["listening", "thinking", "speaking"].includes(String(event.activity))) {
      this.emit("activity", { activity: event.activity as Events["activity"]["activity"] });
    } else if (type === "transcript" && typeof event.text === "string" && ["user", "assistant"].includes(String(event.speaker))) {
      this.emit("transcript", event as unknown as Transcript);
    } else if (type === "error" && typeof event.message === "string") {
      this.emit("error", event as unknown as VoiceError);
    } else if (type === "turn_decision" && typeof event.kind === "string" && typeof event.strategy === "string"
        && typeof event.seq === "number" && typeof event.turn === "number" && typeof event.revision === "number") {
      this.emit("turn_decision", event as unknown as TurnDecision);
    } else if (type === "turn_latency" && typeof event.turn === "number" && typeof event.seq === "number"
        && typeof event.elapsed_ms === "number" && Array.isArray(event.spans)) {
      this.emit("turn_latency", event as unknown as TurnLatency);
    } else if (type === "interruption" || type === "response_start" || type === "response_end") {
      this.emit(type, {});
    }
  }

  private async pollDebug(session: Session, signal: AbortSignal) {
    let cursor = 0;
    while (!signal.aborted) {
      try {
        const page = await this.json(`/sessions/${session.session_id}/events?after=${cursor}`, { signal }, session.token);
        for (const event of page.events) this.emit("debug", event);
        cursor = page.cursor;
        if (page.truncated) this.emit("debug", { kind: "trace_truncated" });
      } catch { if (signal.aborted) return; }
      await new Promise(r => setTimeout(r, 500));
    }
  }
  private async waitFor(predicate: () => boolean, signal: AbortSignal, timeout: number) {
    const start = Date.now();
    while (!predicate()) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      if (Date.now() - start > timeout) throw new Error("WebRTC connection timed out");
      await new Promise(r => setTimeout(r, 50));
    }
  }
  setMuted(muted: boolean): void {
    this.muted = muted;
    this.microphone?.getAudioTracks().forEach(track => { track.enabled = !muted; });
  }
  async enableAudio(): Promise<void> { await this.audio.play(); }

  async disconnect(): Promise<void> {
    ++this.generation;
    this.abort?.abort();
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = undefined;
    this.pc?.close(); this.pc = undefined;
    this.microphone?.getTracks().forEach(track => track.stop()); this.microphone = undefined;
    this.audio.pause(); this.audio.srcObject = null;
    const session = this.session; this.session = undefined;
    this.setState("disconnected");
    if (session) {
      try { await this.json(`/sessions/${session.session_id}`, { method: "DELETE", signal: AbortSignal.timeout(5000) }, session.token); }
      catch { /* Server expiry is the backstop if disconnect signaling fails. */ }
    }
  }
  async dispose(): Promise<void> {
    await this.disconnect();
    this.listeners.clear();
    if (this.ownsAudio) this.audio.remove();
  }
}
