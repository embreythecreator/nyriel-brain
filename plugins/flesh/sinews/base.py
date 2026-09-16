"""Sinew — provider adapter contract for the Flesh organ (WO-F/1).

HOSTAGE-FREE: this module and every Sinew implementation import nothing
from nyriel internals. Config and an HTTP client are injected. This file
is the future service boundary — it must be copy-pasteable into a
standalone Flesh service with zero edits. Vary the limbs, not the spine.
"""

from __future__ import annotations

import json as _json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

VERBS = (
    "move_person",
    "move_object",
    "make_object",
    "perform_physical_task",
    "perform_digital_task",
)

# Normalized lifecycle statuses (WO-F/1 §2.2).
OPEN_STATUSES = ("assigned", "en_route", "arrived", "in_progress")
TERMINAL_STATUSES = ("completed", "canceled", "failed")


class SinewError(Exception):
    """Provider call failed. The message is safe to surface to the operator."""


@dataclass
class Quote:
    sinew: str
    price_usd: float
    eta_minutes: Optional[int]
    provider_quote_id: Optional[str]
    expires_at: Optional[float]  # epoch seconds; None = no provider TTL
    cancellation_terms: str
    raw: dict = field(default_factory=dict)


@dataclass
class DispatchResult:
    provider_reference: str
    status: str  # one of OPEN_STATUSES/TERMINAL_STATUSES
    tracking_url: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class TrackResult:
    status: str
    final_cost_usd: Optional[float] = None
    detail: dict = field(default_factory=dict)


@dataclass
class CancelResult:
    status: str  # canceled | failed
    fee_usd: float = 0.0
    raw: dict = field(default_factory=dict)


class JsonHttpClient:
    """Minimal JSON-over-HTTP client (stdlib only, injectable/mockable).

    request() returns (status_code, parsed_body_dict). HTTP error statuses
    are returned, not raised — sinews decide what a 4xx means. Transport
    failures raise SinewError.
    """

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[dict] = None,
        json_body: Optional[dict] = None,
        timeout: int = 30,
    ) -> tuple:
        data = _json.dumps(json_body).encode("utf-8") if json_body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                return resp.status, (_json.loads(body) if body else {})
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                parsed = _json.loads(body) if body else {}
            except ValueError:
                parsed = {"raw": body[:500].decode("utf-8", "replace")}
            return exc.code, parsed
        except Exception as exc:
            raise SinewError(f"HTTP {method} {url} failed: {exc}") from exc


class Sinew(ABC):
    """One provider adapter. Always pinned explicitly per dispatch — a
    misrouted dispatch spends real money and sends a real human (WO-F/1 §6).
    """

    name: str = ""
    verb: str = ""

    def __init__(self, config: dict, http_client: Any = None):
        self.config = config
        self.http = http_client or JsonHttpClient()

    @abstractmethod
    def quote(self, request: dict) -> Quote: ...

    @abstractmethod
    def dispatch(self, request: dict, idempotency_key: str) -> DispatchResult: ...

    @abstractmethod
    def track(self, provider_reference: str) -> TrackResult: ...

    @abstractmethod
    def cancel(self, provider_reference: str) -> CancelResult: ...


class HandoffSinew(Sinew):
    """C-class provider: no API dispatch exists. dispatch() composes a
    deeplink the operator taps; the provider's own UX enforces T3 from there.
    Flesh records intent only (WO-F/1 §2.2 confirm-is-the-deeplink-tap).
    """

    def dispatch(self, request: dict, idempotency_key: str) -> DispatchResult:
        return DispatchResult(
            provider_reference=self.compose_deeplink(request),
            status="completed",  # intent recorded; nothing trackable follows
            raw={"handoff": True},
        )

    def track(self, provider_reference: str) -> TrackResult:
        return TrackResult(status="completed", detail={"handoff": True})

    def cancel(self, provider_reference: str) -> CancelResult:
        return CancelResult(status="canceled", fee_usd=0.0, raw={"handoff": True})

    @abstractmethod
    def compose_deeplink(self, request: dict) -> str: ...
