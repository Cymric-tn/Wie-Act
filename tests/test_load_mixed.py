"""The mixed load (3 learners, 1 room, 1 prep plan in the background) in virtual time through the constraint simulator:
the v1.3 acceptance numbers, held by a test. It checks the engine's scheduling, not the models."""
import argparse

import pytest

from scripts.load_mixed import densest_window, pct, run_virtual


def args(**kw):
    return argparse.Namespace(**{"duration": 150.0, "pages": 3, "seed": 1, "saturation": 0.05, "no_cache": True, **kw})


@pytest.mark.parametrize("seed", [1, 2])
def test_interactive_latency_stays_bounded_while_a_prep_plan_generates(seed):
    rep = run_virtual(args(seed=seed))
    v = rep["verdict"]
    assert v["zero_exceptions"] and v["zero_server_429"] and v["window_at_most_20"], rep["provider"]
    # Explain is bounded by the engine's own 25 s deadline (a request not ready by then gets fast mode), also when it waited
    # for an identical request in flight; a p95 AT 25 means one learner in twenty got fast mode (reported as "deadline").
    by = rep["interactive"]["by_task"]
    assert rep["interactive"]["p95_s"] <= 25.5
    assert all(d["max_s"] <= 25.5 for t, d in by.items() if t != "ocr_transcribe"), by
    # A photo can wait for the single heavy slot (mid-size and vision share max_concurrent - 1) behind a generation that
    # keeps going into the cache; bounded by one such generation plus the vision call itself.
    assert by.get("ocr_transcribe", {"max_s": 0})["max_s"] < 60
    assert rep["background"]["pages"] >= 1 and rep["interactive"]["n"] > 30
    assert rep["provider"]["by_model"].get("deepseek-ocr", 0) >= 1                 # vision ran in the same window


def test_the_helpers_of_the_report():
    assert pct([1, 2, 3, 4, 5], .5) == 3 and pct([], .5) is None and pct([7], .95) == 7
    assert densest_window([0, 10, 20, 61, 62]) == 4 and densest_window([0, 60, 120]) == 1 and densest_window([]) == 0


# ------------------------------------------------------------------ slot additions: explain share, probe only
def test_the_report_counts_explain_results_degraded_by_the_deadline():
    rep = run_virtual(args(seed=1, duration=120.0))
    ex = rep["interactive"]["explain"]
    assert ex["n"] == rep["interactive"]["by_task"]["explain"]["n"] > 0
    assert 0 <= ex["degraded_by_deadline"] <= ex["n"]
    assert ex["share_degraded_by_deadline"] == round(ex["degraded_by_deadline"] / ex["n"], 3)
    assert ex["degraded_by_deadline"] == rep["interactive"]["degrade_reasons"].get("deadline", 0)


def _probe_engine(counts_vision, monkeypatch):
    import json
    import random
    import time

    import scripts.load_mixed as L
    from engine import Engine
    from gatekeeper.cache import ExactCache
    from gatekeeper.client import LLMClient
    from metrics import MemorySink
    from tests.conftest import FnProvider, chat_ok, make_settings

    state = {"remaining": 20}
    classify = json.dumps({"intent": "EXPLAIN", "language": "FRENCH", "confusion": "LOW", "complexity": 2, "confidence": 0.9})

    def fn(body):
        vision = body["model"] == "deepseek-ocr"
        if counts_vision or not vision:
            state["remaining"] -= 1
        return chat_ok("probe vision" if vision else classify, 50, 10,
                       headers={"x-ratelimit-api_key-remaining-requests": str(state["remaining"])})

    s = make_settings(SIMULATE_CONSTRAINTS="false")
    spy = L.SpyTransport(FnProvider(fn), time.monotonic)
    engine = Engine.create(s, client=LLMClient(s, spy), sink=MemorySink(), cache=ExactCache(":memory:", s.cache_ttl_s),
                           rng=random.Random(1))
    monkeypatch.setattr(L, "real_engine", lambda a: (s, time.monotonic, spy, MemorySink(), engine))
    return L


@pytest.mark.parametrize("counts", [True, False])
def test_probe_only_says_whether_a_vision_request_uses_the_window(monkeypatch, tmp_path, counts):
    import asyncio
    import json
    L = _probe_engine(counts, monkeypatch)
    rep = asyncio.run(L.run_probe(argparse.Namespace(no_cache=True)))
    assert rep["vision_probe"]["vision_request_uses_the_chat_window"] is counts
    assert [x["step"] for x in rep["vision_probe"]["sequence"]] == ["compact-1", "vision", "compact-2"]
    assert rep["provider"]["chat_requests"] == 3 and rep["provider"]["server_429"] == 0 and all(rep["verdict"].values())
    L.write_probe(rep, str(tmp_path / "probe"))
    assert f"vision request uses the 20 / min window: {counts}" in (tmp_path / "probe.md").read_text()
    assert json.load(open(tmp_path / "probe.json"))["exceptions"] == []


# ------------------------------------------------------------------ v1.4: every per-request record is written
def test_the_load_writes_every_per_request_record_to_a_jsonl_file(tmp_path):
    import json

    from scripts.load_mixed import write
    rep = run_virtual(args(seed=1, duration=90.0, setting=["EXPLAIN_EXPECTED_S=10"]))
    prefix = str(tmp_path / "load")
    write(rep, prefix)
    lines = [json.loads(x) for x in open(prefix + ".records.jsonl", encoding="utf-8")]
    kinds = [x["kind"] for x in lines]
    assert kinds[0] == "header" and lines[0]["mode"] == "virtual" and lines[0]["seed"] == 1
    runs = [x for x in lines if x["kind"] == "run"]
    assert len(runs) > 20
    r = runs[0]
    assert {"who", "task", "priority", "t", "latency_s", "degraded", "request_id", "window_remaining_at_start",
            "events", "tokens_in", "tokens_out"} <= set(r)
    assert 0 <= r["window_remaining_at_start"] <= 20 and r["events"] and {"name", "t_ms"} <= set(r["events"][0])
    provider = [x for x in lines if x["kind"] == "provider"]
    assert len(provider) == rep["provider"]["chat_requests"] + sum(
        1 for x in provider if not x["path"].endswith("/chat/completions"))
    assert {"t0", "t1", "model", "status"} <= set(provider[0])
    metric = [x["record"] for x in lines if x["kind"] == "metric"]
    assert metric and {"chat", "run"} <= {m["kind"] for m in metric} and all(m["request_id"] for m in metric)
    saved = json.load(open(prefix + ".json"))
    assert "_records" not in saved and saved["records"]["file"] == "load.records.jsonl"
    assert saved["records"]["lines"] == len(lines)
    # the quota waits and degradations are visible per request
    waits = [e for x in runs for e in x.get("events", []) if e["name"] == "queued" and e.get("data", {}).get("reason") == "quota_window"]
    assert all(w["data"]["eta_s"] > 0 for w in waits)
