"""Jev assessment providers: a `Provider` protocol and `NullProvider`.

Providers never receive exchange credentials and never place or influence an
order directly; `sq.jev.worker` is the only caller, and it writes provider
output to `assessments.jsonl` for `H1JevShadow` (or a human) to read later. No
real model provider exists yet (docs/jev.md, "What is blocked"); its API key
must come from the worker's environment, never from Freqtrade config.
"""

from dataclasses import dataclass
from typing import Protocol

from sq.jev.protocol import DECISIONS

__all__ = [
    "DECISIONS",
    "Assessment",
    "Provider",
    "NullProvider",
    "build_provider",
]


@dataclass
class Assessment:
    """One provider's answer for one candidate, before the worker's envelope
    (timestamps, latency, error) is added.

    `confidence`, when present, is model output as-is and is NOT a calibrated
    probability of trade profit (see docs/jev.md); do not treat it as one in
    any later evaluation.
    """

    decision: str
    rationale: str
    confidence: float | None
    model: str
    model_version: str


class Provider(Protocol):
    """Assesses one candidate. May raise; the worker applies a bounded timeout
    and turns any exception (including a timeout) into a recorded "abstain"
    with the error class name. Implementations must not call anything that
    places, cancels or edits an exchange order."""

    def assess(self, candidate: dict, prompt: str) -> Assessment: ...


class NullProvider:
    """Always abstains; makes no model call of any kind.

    The default provider: it lets shadow mode run end-to-end (candidates
    recorded, a real assessments.jsonl produced) without any model access,
    and is what local dry runs use.
    """

    model = "null"
    model_version = "v0"

    def assess(self, candidate: dict, prompt: str) -> Assessment:
        return Assessment(
            decision="abstain",
            rationale="NullProvider never assesses a candidate; no model is configured.",
            confidence=None,
            model=self.model,
            model_version=self.model_version,
        )


def build_provider(name: str) -> Provider:
    """Construct a provider by name, for CLI/config wiring."""
    if name == "null":
        return NullProvider()
    raise ValueError(f"Unknown Jev provider {name!r}; expected 'null'")
