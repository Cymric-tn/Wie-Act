from scripts.metrics_report import analyze, capacity, pct


def rec(**kw):
    return {"ts": kw.pop("ts", 0.0), "request_id": kw.pop("rid", "r"), **kw}


def test_percentiles_nearest_rank():
    assert pct([], 50) is None and pct([5], 95) == 5
    assert pct(list(range(1, 101)), 50) == 50 and pct(list(range(1, 101)), 95) == 95


def test_analyze_counts_what_the_judges_track():
    recs = [
        rec(ts=0, kind="chat", model="m", model_class="midsize", outcome="saturated", action="retry_after_interval"),
        rec(ts=1, kind="chat", model="m", model_class="midsize", outcome="saturated", action="raise"),
        rec(ts=2, kind="chat", model="c", model_class="compact", outcome="ok", request_hash="h1", tokens_in=10, tokens_out=2),
        rec(ts=3, kind="chat", model="c", model_class="compact", outcome="ok", request_hash="h1", tokens_in=10, tokens_out=2),
        rec(ts=4, kind="chat", model="c", model_class="compact", outcome="cache_hit", from_cache=True, request_hash="h1"),
        rec(ts=5, kind="run", task="explain", latency_ms=100, degraded=True, degrade_reason="midsize_saturated",
            mode="fast_extractive", model_class="compact", priority="INTERACTIVE"),
        rec(ts=6, kind="run", task="classify", latency_ms=0, from_cache=True, priority="BACKGROUND"),
        rec(ts=6, kind="run", task="compress", latency_ms=5),
        rec(ts=7, kind="breaker", outcome="open", detail="closed -> open: 3 saturations"),
        rec(ts=8, kind="breaker", outcome="half_open", detail="open -> half_open: cooldown"),
        rec(ts=9, kind="breaker", outcome="open", detail="half_open -> open: probe saturated"),
    ]
    m = analyze(recs)
    assert m[("volume", "provider requests (all attempts)")] == 4           # cache hit is not a request
    assert m[("volume", "engine.run calls (caller-visible)")] == 2 and m[("volume", "nested compress runs")] == 1
    assert m[("calls per model", "m (midsize) ok / attempts")] == "0 / 2"
    assert m[("class split (ok calls)", "compact")] == "2 (100%)"
    assert m[("tokens", "input tokens")] == 20
    assert m[("cache", "cache hit rate")] == "50%" and m[("cache", "repeated identical provider requests")] == 1
    assert m[("refusals -> handling", "saturated -> raise")] == 1
    assert m[("fallbacks / degradation", "explain served fast_extractive")] == 1
    assert m[("circuit breaker", "trips (closed -> open)")] == 1
    assert m[("circuit breaker", "re-opened after failed probe")] == 1
    assert m[("latency (uncached runs, ms)", "explain p50 / p95 (n)")] == "100 / 100 (1)"
    assert ("latency (uncached runs, ms)", "classify p50 / p95 (n)") not in m   # cache hits excluded


def test_capacity_requests_per_turn_and_learner_estimate():
    recs = []
    for t, (attempts, cached) in {"t1": (2, False), "t2": (3, False), "t3": (1, True)}.items():
        recs += [rec(kind="chat", turn_id=t, outcome="ok", latency_ms=3000) for _ in range(attempts)]
        recs += [rec(kind="run", turn_id=t, task="classify", latency_ms=1000, from_cache=cached),
                 rec(kind="run", turn_id=t, task="hint", latency_ms=2000)]
    recs.append(rec(kind="run", task="classify"))                                 # no turn_id: ignored
    m = capacity(recs, quota_per_min=20, max_concurrent=2, learner_rates=[0.5])
    assert m[("capacity (per learner turn)", "learner turns")] == 3
    assert m[("capacity (per learner turn)", "provider requests / turn (all attempts)")] == 2.0
    assert m[("capacity (per learner turn)", "cold turns (no cache hit): n / requests per turn")] == "2 / 2.50"
    sec = "capacity estimate (one key, 20 req/min)"
    assert m[(sec, "concurrency ceiling (2 x 60 / latency, req/min)")] == 40.0     # 3 s per call
    assert m[(sec, "binding limit (req/min)")] == "20.0 (quota)"
    assert m[(sec, "active learners @ 1 turn / 120 s: quota-bound (observed / cold)")] == "20 / 16"
    assert capacity([rec(kind="run")], 20, 2, [1]) == {}


def test_analyze_reports_truncated_outputs_limiter_waits_and_input_truncation():
    recs = [
        rec(ts=0, kind="chat", model="c", model_class="compact", outcome="ok", request_hash="a", tokens_in=100,
            tokens_out=50, truncated_output=True, wait_ms=0),
        rec(ts=1, kind="chat", model="c", model_class="compact", outcome="ok", request_hash="b", tokens_in=100,
            tokens_out=30, wait_ms=2500),
        rec(ts=2, kind="run", task="hint", latency_ms=10, degraded=True, degrade_reason="input_truncated"),
    ]
    m = analyze(recs)
    assert m[("tokens", "output tokens / ok call")] == 40.0
    assert m[("tokens", "outputs cut at the cap (completion_tokens >= cap)")] == 1
    assert m[("limiter", "attempts that waited on the limiter (> 100 ms)")] == 1
    assert m[("limiter", "max limiter wait (s)")] == 2.5
    assert m[("fallbacks / degradation", "degraded: input_truncated")] == 1
