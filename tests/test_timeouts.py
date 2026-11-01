"""v1.2 step 4: per-class, per-priority request timeouts, all under the server's 90 s."""
import json
import random

import httpx
import pytest

from config import ConfigError, ModelClass
from gatekeeper.client import ChatRequest, LLMClient
from gatekeeper.limiter import Limiter
from gatekeeper.retry import CallContext, RetryingCaller, RetryPolicy
from metrics import MemorySink
from tests.conftest import chat_ok, make_settings

MSG = ({"role": "user", "content": "hi"},)


def test_defaults_and_mapping():
    p = RetryPolicy.from_settings(make_settings())
    assert (p.timeout_midsize_interactive_s, p.timeout_midsize_background_s, p.timeout_compact_s) == (40, 85, 30)
    M, C = ModelClass.MIDSIZE, ModelClass.COMPACT
    assert p.timeout_for(M, "INTERACTIVE") == 40 and p.timeout_for(M, None) == 40
    assert p.timeout_for(M, "BACKGROUND") == 85
    assert p.timeout_for(C, "INTERACTIVE") == 30 and p.timeout_for(C, "BACKGROUND") == 30
    assert p.timeout_for(M, "BACKGROUND", "probe") == 15 and p.timeout_for(C, "INTERACTIVE", "probe") == 15


def test_env_overrides_and_every_timeout_stays_under_the_server_limit():
    s = make_settings(TIMEOUT_MIDSIZE_INTERACTIVE_S=35, TIMEOUT_MIDSIZE_BACKGROUND_S=80, TIMEOUT_COMPACT_S=20,
                      BREAKER_PROBE_TIMEOUT_S=8)
    p = RetryPolicy.from_settings(s)
    assert (p.timeout_midsize_interactive_s, p.timeout_midsize_background_s, p.timeout_compact_s,
            p.probe_timeout_s) == (35, 80, 20, 8)
    for var in ("TIMEOUT_MIDSIZE_INTERACTIVE_S", "TIMEOUT_MIDSIZE_BACKGROUND_S", "TIMEOUT_COMPACT_S",
                "REQUEST_TIMEOUT_S", "BREAKER_PROBE_TIMEOUT_S"):
        with pytest.raises(ConfigError, match="SERVER_TIMEOUT_S"):
            make_settings(**{var: 90})
    make_settings(TIMEOUT_MIDSIZE_BACKGROUND_S=95, SERVER_TIMEOUT_S=100)       # the limit itself is configurable


class Capture(httpx.AsyncBaseTransport):
    def __init__(self):
        self.timeouts = []

    async def handle_async_request(self, request):
        self.timeouts.append(request.extensions["timeout"]["read"])
        r = chat_ok("ok")
        r.request = request
        return r


@pytest.mark.parametrize("cls,priority,purpose,expected", [
    (ModelClass.MIDSIZE, "INTERACTIVE", None, 40), (ModelClass.MIDSIZE, "BACKGROUND", None, 85),
    (ModelClass.COMPACT, "INTERACTIVE", None, 30), (ModelClass.MIDSIZE, "BACKGROUND", "probe", 15)])
async def test_the_request_goes_out_with_its_own_timeout(vt, cls, priority, purpose, expected):
    s, cap = make_settings(), Capture()
    rc = RetryingCaller(LLMClient(s, cap), Limiter(s, vt.clock, vt.sleep), MemorySink(), RetryPolicy.from_settings(s),
                        s.input_token_budget, vt.clock, vt.sleep, random.Random(1))
    await rc.chat(ChatRequest(cls, MSG), CallContext(task="t", priority=priority, purpose=purpose))
    assert cap.timeouts == [expected]
