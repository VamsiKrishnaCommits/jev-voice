# The comparison demo

Use headphones to avoid speaker echo. Start with **Silence only**, then repeat with **Jev**. Read only quoted words; bracketed text means actually pause. Start a new conversation for each run.

## 1. Make the early handoff visible

> “What's the difference between…”
>
> [Stay silent for 3 seconds.]
>
> “…a debit card and a credit card?”

The silence baseline should hand off the incomplete question after its threshold. The actual spoken response might arrive later because LLM/TTS latency is separate. In Jev mode, look for a **hold**, followed by a **handoff** after the completed question.

## 2. A correction that changes the answer

> “Help me plan a trip for Friday—actually…”
>
> [Pause for 2 seconds.]
>
> “…Saturday morning, and I only have half a day. What should I prioritize?”

Check that Jev lets you finish the correction and that the assistant answers the final request rather than the earlier fragment.

## 3. An unfinished thought that trails off

> “Could you help me decide between…”
>
> [Stay silent for at least 5 seconds.]
>
Jev can briefly hold, then yield if a clarification becomes appropriate. Even if it keeps holding, the controller hands off after 4 seconds of detected silence. Look for `silence_limit` and `handoff` with reason `max_silence_timeout`; this is a controller fallback, **not a Jev approval**. The assistant should ask what options you are considering. Audible speech also depends on LLM/TTS latency. Exact-duration wait commands are no longer supported.

## 4. A complete short question

> “What's the capital of France?”

Then stop. Jev should yield rather than hold indefinitely. This checks the tradeoff: preventing cutoffs should not require adding long pauses to every completed turn.

## 5. Interrupt the assistant

Ask for a longer answer, then speak while it talks:

> “Actually, stop. Give me just one sentence.”

Both modes support interruption through the same audio pipeline. This is not a Jev-only capability. A short browser jitter-buffer audio tail can remain after cancellation.

## What to measure

Expand **Behind the pause**. Inspect exact request/response details and timestamps. A hold count alone does not establish quality: one correct pause can create several repeated decisions. Count distinct pauses where the user resumed and Jev withheld handoff, erroneous holds on complete turns, premature handoffs, and time from final speech to handoff separately from time to audible reply.

Use identical words/pause lengths in both modes; replaying the same recorded input is necessary for a controlled benchmark. This interactive demo is illustrative, not a measured claim against LiveKit. Full traces are saved under `.traces/jev/` and `.traces/silence/`.
