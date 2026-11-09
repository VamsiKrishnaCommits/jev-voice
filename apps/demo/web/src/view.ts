export const view = `
<header>
  <a class="brand" href="/">jev<span>voice lab</span></a>
  <span class="local">LOCAL DEMO · WEBRTC</span>
</header>
<main>
  <section class="hero">
    <h1>A little room to <em>think.</em></h1>
    <p class="intro">Talk naturally. Compare turn-taking. See where the time goes.</p>
  </section>
  <section class="lab" aria-label="Voice conversation">
    <div class="modes" role="group" aria-label="Turn-taking mode">
      <button id="jev" class="selected" aria-pressed="true">Jev <small>Understands the pause</small></button>
      <button id="silence" aria-pressed="false">Silence only <small>Responds after 0.8 seconds</small></button>
      <button id="livekit" hidden disabled aria-pressed="false">LiveKit v1 <small>Hosted audio model</small></button>
    </div>
    <p id="mode-note" class="note">Start with Jev. Then repeat the same sentence and pauses in Silence only.</p>
    <p id="setup-notice" class="setup-notice" role="status" hidden></p>
    <div class="voice-status">
      <div id="orb" class="orb" data-activity="idle"><span>j.</span></div>
      <div><p id="activity" aria-live="polite">Your turn.</p><p id="connection">Microphone off</p></div>
    </div>
    <div class="controls">
      <button id="connect" class="primary" disabled>Start a conversation ↗</button>
      <button id="mute" hidden>Mute</button>
      <button id="audio" hidden>Enable audio</button>
    </div>
    <p id="error" role="alert"></p>
    <section id="decision-card" class="decision-card" data-tone="idle" aria-label="Turn decision">
      <p id="decision-source" class="eyebrow">JEV · LIVE DECISION</p>
      <div role="status" aria-live="polite" aria-atomic="true">
        <strong id="decision-title">Waiting for speech</strong>
        <p id="decision-detail">Live turn decisions will appear here.</p>
      </div>
      <p id="decision-last">No decision yet</p>
    </section>
    <p class="try-line">Try: “What’s the difference between…” <b>[pause 2–3s]</b> “…a debit card and a credit card?”</p>
    <p id="voice-model" class="note">Loading voice model…</p>
    <p class="privacy">Use headphones. Microphone audio goes to the configured providers; API charges apply.<br>Full conversation traces are saved locally. Nothing is uploaded to GitHub.</p>
  </section>
  <section class="lower">
    <article class="script"><details><summary>More things to try</summary>
      <h2>Change your mind.</h2>
      <p>“Help me plan a trip for Friday—actually…” <b>[pause 2s]</b> “…Saturday morning. I only have half a day.”</p>
      <h2>Leave a thought unfinished.</h2>
      <p>“Could you help me decide between…” <b>[stop for 5s]</b> The bounded fallback should hand off for a short clarification.</p>
      <h2>The midnight museum.</h2>
      <p>“Let’s role-play. You’re my partner in a fictional museum mystery. We’ve heard footsteps. Whisper: what do we do?”</p>
      <p>“I found something behind the painting, but…” <b>[pause 2s]</b> “…it’s a note with my name on it.”</p>
      <p><b>Interrupt mid-answer:</b> “Actually, stop. Explain in one calm sentence.”</p>
      <p class="note">Keep words and pauses the same when comparing modes. Silence only is a fixed-timeout baseline—not LiveKit’s full agent stack. This is a playground, not a benchmark.</p>
    </details></article>
    <article><details><summary>Conversation <span id="turn-count">live transcript</span></summary>
      <div id="transcript"><p class="empty">Your conversation will appear here.</p></div>
    </details></article>
  </section>
  <section class="trace"><details><summary>Behind the pause <span id="counts">decisions · handoffs · timing</span></summary>
    <label><input id="debug" type="checkbox" checked> Collect full decision traces for the next session</label><div id="trace"></div>
  </details></section>
  <footer>Python agent + TypeScript client. Independently swappable turn-taking.</footer>
</main>`;
