"""Public, provider-independent turn-taking contract."""
import os
from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True)
class Turn:
    text: str
    history: list
    speaking: bool
    silence_seconds: float


@dataclass(frozen=True)
class Decision:
    action: Literal["hold", "yield", "uncertain"]
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.action not in {"hold", "yield", "uncertain"}:
            raise ValueError("action must be hold, yield or uncertain")


class TurnTaking(Protocol):
    """Create one independent strategy per conversation.

    decide() can await a model or timer. It must allow asyncio cancellation.
    Runtime, not the strategy, enforces speech safety and stale-result checks.
    """
    def create(self) -> "TurnTaking": ...
    async def decide(self, turn: Turn) -> Decision: ...


@dataclass(frozen=True)
class JevTurnTaking:
    instructions: str = ""
    examples: tuple = ()
    api_key: str | None = field(default=None, repr=False)
    model: str = "jev-latest"

    def create(self):
        return self  # Immutable; each request has its own HTTP lifecycle.

    async def decide(self, turn):
        from jev_pipecat.turns import JevClient
        result = await JevClient(self.api_key or os.getenv("TYPESAFE_API_KEY"),
            model=self.model, instructions=self.instructions, examples=self.examples).evaluate_turn(
                text=turn.text, context=turn.history,
                timing={"speaking": turn.speaking, "silenceMs": round(turn.silence_seconds*1000)})
        return Decision(result["decision"], details=result)


@dataclass(frozen=True)
class SilenceTurnTaking:
    seconds: float = .8

    def __post_init__(self):
        import math
        if not math.isfinite(self.seconds) or self.seconds < 0:
            raise ValueError("seconds must be finite and nonnegative")

    def create(self):
        return self

    async def decide(self, turn):
        import asyncio
        if turn.speaking:
            return Decision("hold")
        await asyncio.sleep(max(0, self.seconds - turn.silence_seconds))
        return Decision("yield", details={"source": "silence", "threshold_seconds": self.seconds})


class StrategyAdapter:
    """Private bridge to the existing controller; not part of the public API."""
    def __init__(self, strategy):
        self.strategy = strategy.create()

    async def evaluate_turn(self, *, text, context, timing):
        result = await self.strategy.decide(Turn(text, context, timing["speaking"],
            timing["silenceMs"]/1000))
        if not isinstance(result, Decision):
            raise TypeError("TurnTaking.decide must return Decision")
        return {**result.details, "decision": result.action}
