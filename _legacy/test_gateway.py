"""
Run against the MOCK (key-free, off-slot):
  terminal 1:  uvicorn mock_server:app --port 8099
  terminal 2:  LLM_BASE_URL=http://localhost:8099/v1 LLM_API_KEY=mock python test_gateway.py
Each scenario prints PASS/FAIL. Then check:  curl localhost:8099/admin/stats
"""
import asyncio, os, httpx
from gateway import Gateway, GatewayError

ADMIN = os.environ["LLM_BASE_URL"].rsplit("/v1", 1)[0] + "/admin"
MSG = lambda s: [{"role": "user", "content": s}]


async def fault(mode, prob=1.0):
    async with httpx.AsyncClient() as c:
        await c.post(f"{ADMIN}/fault", json={"mode": mode, "prob": prob})


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name, extra)


async def main():
    if os.path.exists("cache_test.sqlite"):
        os.remove("cache_test.sqlite")
    gw = Gateway(metrics_path="metrics_test.jsonl", cache_path="cache_test.sqlite")
    log = []
    st = log.append

    await fault("none")
    r = await gw.call("compact", MSG("test normal compact"), on_status=st)
    check("1 normal compact", r.tier_used == "compact" and not r.cached)

    r2 = await gw.call("compact", MSG("test   normal COMPACT"), on_status=st)
    check("2 cache hit on normalized duplicate", r2.cached)

    r = await gw.call("compact", MSG("json please"), json_mode=True, use_cache=False)
    try:
        r.json(); ok = True
    except Exception:
        ok = False
    check("3 json parse", ok)

    await fault("mid_saturated")
    log.clear()
    r = await gw.call("mid", MSG("explain derivatives (saturated)"), on_status=st, use_cache=False)
    check("4 mid saturated -> fallback to compact", r.fell_back and r.tier_used == "compact",
          [e["stage"] for e in log])

    await fault("cooldown")
    log.clear()
    try:
        r = await gw.call("mid", MSG("cooldown case"), on_status=st, use_cache=False)
        check("5 cooldown everywhere -> graceful error", False, "expected GatewayError")
    except GatewayError as e:
        check("5 cooldown everywhere -> graceful error", True, str(e)[:60])

    await fault("ceiling")
    try:
        await gw.call("compact", MSG("ceiling"), use_cache=False)
        check("6 400 surfaces as GatewayError", False)
    except GatewayError:
        check("6 400 surfaces as GatewayError", True)

    try:
        await gw.call("mid", MSG("x" * 9000), use_cache=False)
        check("7 oversized input blocked client-side", False)
    except GatewayError:
        check("7 oversized input blocked client-side", True)

    await fault("none")
    res = await asyncio.gather(*[gw.call("compact", MSG(f"burst {i}"), use_cache=False)
                                 for i in range(6)], return_exceptions=True)
    check("8 burst of 6 under concurrency cap", all(not isinstance(x, Exception) for x in res))

    await gw.aclose()
    print("\nmetrics -> metrics_test.jsonl ; mock view -> curl", ADMIN + "/stats")


asyncio.run(main())
