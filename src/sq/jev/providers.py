"""Jev assessment providers: a `Provider` protocol plus two implementations.

Providers never receive exchange credentials and never place or influence an
order directly; `sq.jev.worker` is the only caller, and it writes provider
output to `assessments.jsonl` for `H1JevShadow` (or a human) to read later. A
model API key, when one exists, comes from an environment variable only (e.g.
`JEV_API_KEY`), never from Freqtrade config, per AGENTS.md.
"""

import os
from dataclasses import dataclass
from typing import Protocol

from sq.jev.protocol import DECISIONS

__all__ = [
    "DECISIONS",
    "Assessment",
    "Provider",
    "NullProvider",
    "JevProvider",
    "build_provider",
]


@dataclass
class Assessment:
    """One provider's answer for one candidate, before the worker's envelope
    (timestamps, latency, error) is added.

    `confidence`, when present, is model output as-is and is NOT a calibrated
    probability of trade profit (see docs/jev.md and
    AGENTS.md); do not treat it as one in any later evaluation.
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
    and is what tests and local dry runs use.
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


class JevProvider:
    """Stub for a real Jev API integration. Not implemented.

    This repository has no Jev API access or documentation, so this class
    intentionally does not perform any network call, and `assess()` always
    raises `NotImplementedError`. A real implementation needs, at minimum:

      - an endpoint URL and an authentication scheme (the key would come from
        an environment variable only, e.g. `JEV_API_KEY`, never from
        Freqtrade config or a committed file);
      - the request schema for a candidate + prompt pair (field names, types,
        encoding);
      - the response schema mapping to decision/rationale/confidence/model
        metadata, including how "abstain" is distinguished from an error;
      - pricing and rate limits, to size the worker's timeout, bounded queue
        and any retry/backoff policy (this stub adds none).

    Until that information exists, do not add HTTP calls or invent field
    names here.
    """

    def __init__(self, *, api_key: str | None = None, **_unused_kwargs):
        self.api_key = api_key if api_key is not None else os.environ.get("JEV_API_KEY")

    def assess(self, candidate: dict, prompt: str) -> Assessment:
        raise NotImplementedError(
            "JevProvider is an unimplemented stub: no Jev API endpoint, auth scheme, "
            "request/response schema, pricing or rate limits are known. Needs: endpoint "
            "URL, an env-var-only auth scheme, request schema, response schema, pricing, "
            "rate limits. See this class's docstring."
        )


def build_provider(name: str) -> Provider:
    """Construct a provider by name, for CLI/config wiring."""
    if name == "null":
        return NullProvider()
    if name == "jev":
        return JevProvider()
    raise ValueError(f"Unknown Jev provider {name!r}; expected 'null' or 'jev'")
