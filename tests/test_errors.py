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


def test_quota_delay_from_message_when_no_header():
    e = classify_response(error(429, "rate_limit_error", "Team quota exhausted. Retry in 12.5s"))
    assert isinstance(e, QuotaExhausted) and e.retry_after_s == 12.5


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
