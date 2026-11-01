import json
import os

import httpx
import pytest

from gatekeeper.errors import (CeilingExceeded, ConcurrencyLimited, FatalError, ModelSaturated, QuotaExhausted,
                               TransientError, classify_exception, classify_response, parse_quota)
from tests.conftest import chat_ok, error

# Verbatim from recon_log.jsonl (real FelCloud, 2026-10-22)
REAL_COOLDOWN = error(
    429, "all_deployments_in_cooldown",
    "No deployments available for selected model, Try again in 5 seconds. All deployments for selected "
    "model are in cooldown. Passed model=llama3.2-3b. pre-call-checks=False, cooldown_list=['98a8']",
    {"retry-after": "5", "x-litellm-key-rpm-limit": "20"})
REAL_200_HEADERS = {"x-litellm-key-rpm-limit": "20", "x-ratelimit-api_key-limit-max_parallel_requests": "2",
                    "x-ratelimit-api_key-limit-requests": "20",
                    "x-ratelimit-api_key-remaining-max_parallel_requests": "1",
                    "x-ratelimit-api_key-remaining-requests": "19"}


def test_success_is_none():
    assert classify_response(chat_ok()) is None


def test_real_felcloud_cooldown_is_saturation():
    e = classify_response(REAL_COOLDOWN)
    assert isinstance(e, ModelSaturated) and e.retry_after_s == 5


def test_real_felcloud_cooldown_exact_recorded_response():
    """Replays the full recorded 429 (body incl. param/code, all headers) from recon_log.jsonl."""
    if not os.path.exists("recon_log.jsonl"):
        pytest.skip("recon_log.jsonl not present")
    rec = next(r for r in map(json.loads, open("recon_log.jsonl")) if r.get("status") == 429)
    headers = {k: v for k, v in rec["headers"].items() if k != "content-length"}
    e = classify_response(httpx.Response(429, json=rec["err"], headers=headers))
    assert isinstance(e, ModelSaturated) and (e.status, e.retry_after_s) == (429, 5)
    assert parse_quota(httpx.Headers(headers)).remaining_requests is None   # no quota headers on errors


def test_real_felcloud_quota_headers():
    q = parse_quota(httpx.Headers(REAL_200_HEADERS))
    assert (q.remaining_requests, q.limit_requests, q.remaining_parallel, q.limit_parallel) == (19, 20, 1, 2)


def test_missing_quota_headers_are_none():
    assert parse_quota(httpx.Headers({})).remaining_requests is None


def test_annex_503_is_saturation_with_retry_after():
    e = classify_response(error(503, "model_saturated", "busy", {"Retry-After": "12"}))
    assert isinstance(e, ModelSaturated) and e.retry_after_s == 12


def test_503_without_retry_after_gets_default():
    e = classify_response(httpx.Response(503, text="Service Unavailable"))
    assert isinstance(e, ModelSaturated) and e.retry_after_s > 0


def test_quota_delay_from_header():
    e = classify_response(error(429, "quota_exhausted", "Team quota exhausted", {"Retry-After": "17"}))
    assert isinstance(e, QuotaExhausted) and e.retry_after_s == 17


def test_quota_delay_is_the_header_only_never_the_message():
    e = classify_response(error(429, "rate_limit_error", "Team quota exhausted. Retry in 12.5s"))
    assert isinstance(e, QuotaExhausted) and e.retry_after_s is None
    e = classify_response(error(429, "rate_limit_error", "Rate limit exceeded. Retry in 12.5s",
                                {"retry-after": "60"}))
    assert e.retry_after_s == 60.0


def test_quota_without_any_delay():
    e = classify_response(error(429, "rate_limit_error", "Rate limit exceeded"))
    assert isinstance(e, QuotaExhausted) and e.retry_after_s is None


def test_retry_after_http_date():
    e = classify_response(error(429, "x", "quota", {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}))
    assert isinstance(e, QuotaExhausted) and e.retry_after_s == 0.0          # already in the past


def test_concurrency_limit():
    e = classify_response(error(429, "concurrency_limit", "Max parallel requests reached", {"Retry-After": "2"}))
    assert isinstance(e, ConcurrencyLimited) and e.retry_after_s == 2


@pytest.mark.parametrize("msg,ceiling,limit", [
    ("max_tokens 9000 exceeds ceiling 4096", "max_tokens", 4096),
    ("prompt is 3000 tokens, exceeds ceiling 2048", "prompt_tokens", 2048),
    ("This model's maximum context length is 2048 tokens. However, you requested 3000 tokens",
     "prompt_tokens", 2048),
    ("Request exceeds the ceiling of 1024", "unknown", 1024),
])
def test_ceilings_are_named(msg, ceiling, limit):
    e = classify_response(error(400, "invalid_request_error", msg))
    assert isinstance(e, CeilingExceeded) and (e.ceiling, e.limit) == (ceiling, limit)


@pytest.mark.parametrize("status,msg", [(400, "unknown model foo"), (400, "input must be a string"),
                                        (401, "auth_error"), (404, "Not Found")])
def test_fatal(status, msg):
    assert isinstance(classify_response(error(status, "invalid_request_error", msg)), FatalError)


@pytest.mark.parametrize("status", [500, 502, 504, 408])
def test_transient(status):
    assert isinstance(classify_response(httpx.Response(status, text="oops")), TransientError)


def test_exceptions():
    assert isinstance(classify_exception(httpx.ReadTimeout("t")), TransientError)
    assert isinstance(classify_exception(httpx.ConnectError("c")), TransientError)
    assert isinstance(classify_exception(RuntimeError("x")), FatalError)


def test_error_str_is_short_and_has_no_headers():
    s = str(classify_response(REAL_COOLDOWN))
    assert s.startswith("saturated[429]") and len(s) < 220


# ---------------------------------------------------------------------------------------------------
# The two 429s FelCloud answered on 2026-10-26 (reports/felcloud-2026-10-26.md, probes A and 6): real status,
# headers and body. Only the hash of the API key in the body is redacted (<key-hash>); content-length is
# the original value, so it no longer matches the redacted body.
REAL_429 = {
    "concurrency": {
        "status": 429,
        "headers": {
            "alt-svc": "h3=\":443\"; ma=2592000",
            "content-length": "282",
            "content-security-policy": "frame-ancestors 'none'",
            "content-type": "application/json",
            "date": "Mon, 26 Oct 2026 17:01:24 GMT",
            "rate_limit_type": "max_parallel_requests",
            "reset_at": "2026-10-26 17:02:25 UTC",
            "retry-after": "60",
            "vary": "Origin",
            "via": "1.1 Caddy",
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "x-litellm-call-id": "6c59c0ab-e654-4b50-9edc-26c854f0540b",
            "x-litellm-key-rpm-limit": "20",
            "x-litellm-key-spend": "0.0",
            "x-litellm-response-cost": "0"
        },
        "body": "{\"error\":{\"message\":\"Rate limit exceeded for api_key: <key-hash>. Limit type: max_parallel_requests. Current limit: 2, Remaining: 0. Limit resets at: 2026-10-26 17:02:25 UTC\",\"type\":\"throttling_error\",\"param\":null,\"code\":\"429\"}}"
    },
    "quota": {
        "status": 429,
        "headers": {
            "alt-svc": "h3=\":443\"; ma=2592000",
            "content-length": "270",
            "content-security-policy": "frame-ancestors 'none'",
            "content-type": "application/json",
            "date": "Mon, 26 Oct 2026 17:02:33 GMT",
            "rate_limit_type": "requests",
            "reset_at": "2026-10-26 17:03:34 UTC",
            "retry-after": "60",
            "vary": "Origin",
            "via": "1.1 Caddy",
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "x-litellm-call-id": "ef80a0bd-70fc-4f91-8242-d63939dc2919",
            "x-litellm-key-rpm-limit": "20",
            "x-litellm-key-spend": "0.0",
            "x-litellm-response-cost": "0"
        },
        "body": "{\"error\":{\"message\":\"Rate limit exceeded for api_key: <key-hash>. Limit type: requests. Current limit: 20, Remaining: 0. Limit resets at: 2026-10-26 17:03:34 UTC\",\"type\":\"throttling_error\",\"param\":null,\"code\":\"429\"}}"
    }
}


def real_429(kind: str) -> httpx.Response:
    r = REAL_429[kind]
    return httpx.Response(r["status"], headers=r["headers"], content=r["body"].encode())


def test_real_quota_429_is_quota_exhausted_with_the_header_delay():
    e = classify_response(real_429("quota"))
    assert isinstance(e, QuotaExhausted) and (e.status, e.retry_after_s) == (429, 60.0)
    assert "Limit type: requests" in e.message


def test_real_concurrency_429_is_concurrency_limited():
    e = classify_response(real_429("concurrency"))
    assert isinstance(e, ConcurrencyLimited) and (e.status, e.retry_after_s) == (429, 60.0)
    assert "max_parallel_requests" in e.message


def test_real_quota_429_ignores_limit_resets_at_in_the_body():
    """'Limit resets at' is always the refusal time + 60 s: with no retry-after header there is no delay."""
    r = REAL_429["quota"]
    headers = {k: v for k, v in r["headers"].items() if k.lower() != "retry-after"}
    e = classify_response(httpx.Response(429, headers=headers, content=r["body"].encode()))
    assert isinstance(e, QuotaExhausted) and e.retry_after_s is None


def test_limit_type_header_alone_tells_concurrency_from_quota():
    for kind, cls in (("quota", QuotaExhausted), ("concurrency", ConcurrencyLimited)):
        r = REAL_429[kind]
        bare = httpx.Response(429, headers={"retry-after": "60", "rate_limit_type": r["headers"]["rate_limit_type"]},
                              json={"error": {"message": "Rate limit exceeded", "type": "throttling_error"}})
        assert isinstance(classify_response(bare), cls)


def test_key_hash_is_scrubbed_from_error_messages():
    from gatekeeper.errors import FatalError, scrub
    raw = "Rate limit exceeded for api_key: 3f9a1c0be47d55aa90d1c2e3f4a5b6c7. Limit type: requests."
    assert "3f9a1c0be47d" not in scrub(raw)
    assert "api_key: <key-hash>" in scrub(raw)
    assert "<key-hash>" in str(FatalError(status=401, message=raw))
    assert "sk-abcdef123456" not in FatalError(message="bad key sk-abcdef123456xyz").message
    assert scrub("Limit type: requests. Current limit: 20") == "Limit type: requests. Current limit: 20"
