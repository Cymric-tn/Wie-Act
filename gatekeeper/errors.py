"""
Maps provider responses to typed errors. ALL knowledge of status codes, error types and
header names lives here, so a FelCloud contract change is a one-line fix.

Evidence: recon_log.jsonl (real FelCloud, 2026-10-22) — a LiteLLM 1.103 proxy — and the quota / concurrency
429s captured on 2026-10-26 (reports/felcloud-2026-10-26.md).
"""
from __future__ import annotations

import email.utils
import re
import time
from dataclasses import dataclass

import httpx

# CONFIRMED(felcloud): quota header names, present on every 200 during recon (absent on errors).
H_REMAINING_REQUESTS = "x-ratelimit-api_key-remaining-requests"
H_LIMIT_REQUESTS = "x-ratelimit-api_key-limit-requests"
H_REMAINING_PARALLEL = "x-ratelimit-api_key-remaining-max_parallel_requests"
H_LIMIT_PARALLEL = "x-ratelimit-api_key-limit-max_parallel_requests"
H_RESET = "x-ratelimit-api_key-reset-requests"     # ASSUMPTION(felcloud): NOT observed; harmless if absent
H_RETRY_AFTER = "retry-after"

DEFAULT_RETRY_AFTER_S = 5.0                         # recon: LiteLLM cooldown says 5 s


# The proxy echoes a hash of the team's API key in its 429 bodies ("Rate limit exceeded for api_key: <hash>").
# It is a credential fingerprint: it must not reach metrics, logs, degrade notes or reports.
_KEY_HASH = re.compile(r"(api[_ -]?key(?: hash)?\s*[:=]\s*)[A-Za-z0-9_\-]{8,}", re.I)
_BEARER = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,})")


def scrub(text: str) -> str:
    """Replace key hashes and key-looking tokens in provider text with <key-hash> / <redacted>."""
    return _BEARER.sub("<redacted>", _KEY_HASH.sub(r"\1<key-hash>", text or ""))


@dataclass(eq=False)
class LLMError(Exception):
    kind: str = "error"
    status: int | None = None
    message: str = ""
    retry_after_s: float | None = None
    attempts: int = 0                 # set by retry.py when it gives up

    def __post_init__(self) -> None:
        self.message = scrub(self.message)

    def __str__(self) -> str:
        ra = f", retry_after={self.retry_after_s:g}s" if self.retry_after_s is not None else ""
        return f"{self.kind}[{self.status}]{ra}: {self.message[:160]}"


@dataclass(eq=False)
class QuotaExhausted(LLMError):
    kind: str = "quota_exhausted"


@dataclass(eq=False)
class ModelSaturated(LLMError):
    kind: str = "saturated"


@dataclass(eq=False)
class ConcurrencyLimited(LLMError):
    kind: str = "concurrency_limited"
    after_timeout: bool = False       # refused while a timed-out request of the class is still counted: fall back


@dataclass(eq=False)
class ClassUnavailable(LLMError):
    """Raised locally, nothing was sent: the model class's breaker opened while the request waited its turn.
    retry_after_s = seconds until the class is probed again."""
    kind: str = "class_unavailable"


@dataclass(eq=False)
class CeilingExceeded(LLMError):
    kind: str = "ceiling"
    ceiling: str = "unknown"          # "max_tokens" | "prompt_tokens" | "unknown"
    limit: int | None = None


@dataclass(eq=False)
class TransientError(LLMError):
    kind: str = "transient"
    timeout: bool = False             # the request got no answer within the client timeout (a hung model)


@dataclass(eq=False)
class FatalError(LLMError):
    kind: str = "fatal"


@dataclass(frozen=True)
class QuotaInfo:
    remaining_requests: int | None = None
    limit_requests: int | None = None
    remaining_parallel: int | None = None
    limit_parallel: int | None = None


def _int(v: str | None) -> int | None:
    try:
        return int(float(v)) if v is not None else None
    except ValueError:
        return None


def parse_quota(headers: httpx.Headers) -> QuotaInfo:
    return QuotaInfo(
        remaining_requests=_int(headers.get(H_REMAINING_REQUESTS)),
        limit_requests=_int(headers.get(H_LIMIT_REQUESTS)),
        remaining_parallel=_int(headers.get(H_REMAINING_PARALLEL)),
        limit_parallel=_int(headers.get(H_LIMIT_PARALLEL)),
    )


_SECONDS_IN_MSG = re.compile(r"(?:retry|try again)\s+(?:in|after)\s+(\d+(?:\.\d+)?)\s*s", re.I)


def retry_after_header(resp: httpx.Response) -> float | None:
    """The Retry-After header alone (seconds or HTTP date)."""
    v = resp.headers.get(H_RETRY_AFTER)
    if v:
        try:
            return max(0.0, float(v))
        except ValueError:
            try:
                return max(0.0, email.utils.parsedate_to_datetime(v).timestamp() - time.time())
            except (TypeError, ValueError):
                pass
    return None


def parse_retry_after(resp: httpx.Response, message: str = "") -> float | None:
    """Retry-After (seconds or HTTP date) -> reset header -> 'try again in N seconds' in the message."""
    if (h := retry_after_header(resp)) is not None:
        return h
    v = resp.headers.get(H_RESET)
    if v:
        try:
            return max(0.0, float(v))
        except ValueError:
            pass
    m = _SECONDS_IN_MSG.search(message)
    return float(m.group(1)) if m else None


def _error_fields(resp: httpx.Response) -> tuple[str, str]:
    try:
        data = resp.json()
    except ValueError:
        return "", resp.text[:300]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("type") or ""), str(err.get("message") or "")
    if isinstance(err, str):
        return "", err
    return "", str(data.get("detail") or data)[:300] if isinstance(data, dict) else ""


_NUM = re.compile(r"\d+")


def _ceiling(etype: str, msg: str) -> tuple[str, int | None] | None:
    t = f"{etype} {msg}".lower()
    if "max_tokens" in t or "max tokens" in t or "max_completion_tokens" in t:
        kind = "max_tokens"
    elif (any(k in t for k in ("context", "prompt", "input"))
          and any(k in t for k in ("token", "length", "too long", "exceed", "ceiling"))):
        kind = "prompt_tokens"
    elif "ceiling" in t:
        kind = "unknown"
    else:
        return None
    # ASSUMPTION(felcloud): the limit is the number after 'ceiling'/'maximum ... is'/'limit',
    # else after 'exceeds', else the last number. Searched in the message only (not the type).
    m = msg.lower()
    for pat in (r"(?:ceiling|limit)(?: is| of)?\D{0,12}?(\d+)", r"maximum[\w ]{0,30}? is\D{0,5}(\d+)",
                r"exceeds?\D{0,20}(\d+)"):
        if (hit := re.search(pat, m)):
            return kind, int(hit.group(1))
    nums = _NUM.findall(m)
    return kind, int(nums[-1]) if nums else None


def classify_response(resp: httpx.Response) -> LLMError | None:
    """None for success, else the typed error. The single place that interprets provider replies."""
    s = resp.status_code
    if 200 <= s < 300:
        return None
    etype, msg = _error_fields(resp)
    low = f"{etype} {msg} {resp.headers.get('rate_limit_type', '')}".lower()   # FelCloud 429s name the limit type in a header too
    ra = parse_retry_after(resp, msg)

    # CONFIRMED(felcloud, recon_log.jsonl 2026-10-22 15:18:57): LiteLLM cooldown arrives as
    #   status 429
    #   body    {"error": {"message": "No deployments available for selected model, Try again in 5 seconds.
    #            All deployments for selected model are in cooldown. Passed model=llama3.2-3b. ...",
    #            "type": "all_deployments_in_cooldown", "param": null, "code": "429"}}
    #   headers retry-after: 5, x-litellm-key-rpm-limit: 20, x-litellm-version: 1.103.0
    #           (no x-ratelimit-api_key-* headers on this error)
    # Seen on /embeddings after two 404s (failures put the deployment in cooldown), so the shape is
    # confirmed; that load produces the same shape is inferred. 503 + Retry-After is the annex contract.
    if s == 503 or (s == 429 and ("cooldown" in low or "no deployments available" in low
                                  or "saturat" in low)):
        return ModelSaturated(status=s, message=msg, retry_after_s=ra if ra is not None else DEFAULT_RETRY_AFTER_S)
    if s == 429:
        # CONFIRMED(felcloud, probes 2026-10-26, reports/felcloud-raw.jsonl): the API-key limits answer
        #   status 429, header retry-after: 60, body {"error": {"message": "Rate limit exceeded for api_key: <hash>.
        #   Limit type: max_parallel_requests|requests. Current limit: N, Remaining: 0. Limit resets at: <UTC>",
        #   "type": "throttling_error", "param": null, "code": "429"}}
        # "Limit resets at" is always the refusal time + 60 s (not the real reset): never parse it.
        # Concurrency: limit type max_parallel_requests (limit 2); the slot frees as soon as a request ends.
        if "parallel" in low or "concurren" in low:
            return ConcurrencyLimited(status=s, message=msg, retry_after_s=ra if ra is not None else 1.0)
        # Quota: limit type requests (20 per rolling minute). The stated delay is the retry-after header only.
        return QuotaExhausted(status=s, message=msg, retry_after_s=retry_after_header(resp))
    if s in (400, 413, 422):
        c = _ceiling(etype, msg)
        if c:
            return CeilingExceeded(status=s, message=msg, ceiling=c[0], limit=c[1])
        return FatalError(status=s, message=msg)
    if s in (408, 409, 425) or s >= 500:
        return TransientError(status=s, message=msg, retry_after_s=ra)
    return FatalError(status=s, message=msg)       # 401/403/404...: retrying will not help


def classify_exception(exc: BaseException) -> LLMError:
    if isinstance(exc, httpx.TimeoutException):
        return TransientError(message=f"timeout: {type(exc).__name__}", timeout=True)
    if isinstance(exc, httpx.TransportError):
        return TransientError(message=f"network: {type(exc).__name__}")
    return FatalError(message=f"{type(exc).__name__}: {exc}")
