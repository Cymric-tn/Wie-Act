"""scripts/slot_run.sh: the order of the Monday sequence, the files it writes, and the stop rule (a server 429, an
exception, or a step that died without a report). The provider-facing steps are replaced by a stub interpreter; the
guard, the explain share and the scan are the real code."""
import argparse
import json
import os
import subprocess
import sys

import pytest

from scripts import slot_guard
from scripts.explain_deadline import share

REAL_PY = sys.executable
STUB = r'''#!/usr/bin/env bash
# a stand-in for `python`: provider steps are scripted, the rest is the real interpreter
echo "$*" >> "$STUB_LOG"
mod=${2:-}
arg() { local want=$1; shift; while [ $# -gt 0 ]; do [ "$1" = "$want" ] && { echo "$2"; return; }; shift; done; }
fail_kind() { [ "${STUB_FAIL_STEP:-}" = "$1" ] && echo "${STUB_FAIL_KIND:-}"; }
case "$mod" in
  scripts.slot_guard|scripts.explain_deadline) exec "$REAL_PY" "$@" ;;
  scripts.smoke)
    out=$(arg --guard-out "$@"); k=$(fail_kind smoke); echo "smoke output (ocr_transcribe ok)"
    [ "$k" = crash ] && exit 1
    n429=0; exc="[]"; [ "$k" = 429 ] && n429=2; [ "$k" = exception ] && exc='[{"task":"explain","error":"X"}]'
    echo "{\"chat_requests\": 9, \"server_429\": $n429, \"exceptions\": $exc}" > "$out" ;;
  scripts.eval_check_claim)
    out=$(arg --guard-out "$@"); k=$(fail_kind eval)
    n429=0; [ "$k" = 429 ] && n429=1
    echo "{\"chat_requests\": 19, \"server_429\": $n429, \"exceptions\": []}" > "$out"; echo '{"summary": {}}' > "$(arg --out "$@")" ;;
  scripts.load_mixed)
    out=$(arg --out "$@"); k=$(fail_kind load); n429=0; [ "$k" = 429 ] && n429=3
    if echo "$*" | grep -q -- --probe-only; then
      k=$(fail_kind probe); n429=0; [ "$k" = 429 ] && n429=1
      echo "{\"provider\": {\"server_429\": $n429}, \"exceptions\": [], \"vision_probe\": {}}" > "$out.json"; echo "# probe" > "$out.md"
    else
      echo "{\"mode\": \"real\", \"cache\": \"off\", \"provider\": {\"server_429\": $n429}, \"exceptions\": [],
             \"interactive\": {\"explain\": {\"n\": 8, \"degraded_by_deadline\": 2, \"share_degraded_by_deadline\": 0.25},
             \"by_task\": {\"explain\": {\"p50_s\": 9.0, \"p95_s\": 25.0, \"max_s\": 25.0}}}}" > "$out.json"; echo "# load" > "$out.md"
      [ "${STUB_LOAD_VERDICT_FAILS:-}" = 1 ] && exit 1
    fi ;;
esac
exit 0
'''


@pytest.fixture
def run_slot(tmp_path):
    stub = tmp_path / "stubpy"
    stub.write_text(STUB)
    stub.chmod(0o755)
    env_file = tmp_path / "env.test"
    env_file.write_text("LLM_API_KEY=live-key-0123456789abcdef\n")
    work = tmp_path / "repo"
    (work / "scripts").mkdir(parents=True)
    # the script cd's to its own repo root: give it a copy next to a scripts/ directory of its own
    script = work / "scripts" / "slot_run.sh"
    script.write_text(open("scripts/slot_run.sh").read())
    script.chmod(0o755)
    log = tmp_path / "calls.log"

    def go(**env):
        e = {**os.environ, "PYTHON": str(stub), "REAL_PY": REAL_PY, "STUB_LOG": str(log), "ENV_FILE": str(env_file),
             "SLOT_GAP_S": "0", "SLOT_DATE": "2026-11-02", "PYTHONPATH": os.getcwd(), **env}
        p = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=e, cwd=work, timeout=60)
        calls = log.read_text().splitlines() if log.exists() else []
        return p, calls, sorted(os.listdir(work / "reports")) if (work / "reports").exists() else []
    go.env_file = env_file
    return go


def test_the_sequence_runs_in_order_and_writes_every_report(run_slot):
    p, calls, files = run_slot()
    assert p.returncode == 0, p.stdout + p.stderr
    modules = [c.split()[1] for c in calls if c.startswith("-m")]
    assert [m for m in modules if m != "scripts.slot_guard"] == ["scripts.smoke", "scripts.load_mixed", "scripts.eval_check_claim",
                                                                 "scripts.load_mixed", "scripts.explain_deadline"]
    assert "--probe-only" in calls[[i for i, c in enumerate(calls) if "load_mixed" in c][0]]
    assert "--no-cache" in [c for c in calls if "load_mixed" in c and "--probe-only" not in c][0]
    assert "--no-cache" in [c for c in calls if "scripts.smoke" in c][0]
    assert all(f.startswith("felcloud-2026-11-02-") for f in files)
    assert {"felcloud-2026-11-02-smoke.log", "felcloud-2026-11-02-smoke.guard.json", "felcloud-2026-11-02-vision-probe.json",
            "felcloud-2026-11-02-vision-probe.md", "felcloud-2026-11-02-check-claim.json",
            "felcloud-2026-11-02-check-claim.guard.json", "felcloud-2026-11-02-load-mixed.json",
            "felcloud-2026-11-02-load-mixed.md", "felcloud-2026-11-02-explain-deadline.json",
            "felcloud-2026-11-02-explain-deadline.md"} <= set(files)
    assert "slot run complete" in p.stdout


def test_the_explain_share_comes_from_the_load_report(run_slot, tmp_path):
    p, _, _ = run_slot()
    out = json.load(open(tmp_path / "repo" / "reports" / "felcloud-2026-11-02-explain-deadline.json"))
    assert out["interactive_explain"] == 8 and out["degraded_by_deadline"] == 2 and out["share"] == 0.25
    assert "25.0%" in open(tmp_path / "repo" / "reports" / "felcloud-2026-11-02-explain-deadline.md").read()


@pytest.mark.parametrize("step,kind,stopped_at,not_run", [
    ("smoke", "429", "a", ["scripts.load_mixed", "scripts.eval_check_claim", "scripts.explain_deadline"]),
    ("smoke", "exception", "a", ["scripts.load_mixed", "scripts.eval_check_claim"]),
    ("smoke", "crash", "a", ["scripts.load_mixed", "scripts.eval_check_claim"]),              # died without a report
    ("probe", "429", "b", ["scripts.eval_check_claim", "scripts.explain_deadline"]),
    ("eval", "429", "c", ["scripts.explain_deadline"]),
    ("load", "429", "d", ["scripts.explain_deadline"]),
])
def test_a_server_429_or_an_exception_stops_the_sequence(run_slot, step, kind, stopped_at, not_run):
    p, calls, files = run_slot(STUB_FAIL_STEP=step, STUB_FAIL_KIND=kind)
    assert p.returncode == 1 and f"SEQUENCE STOPPED at step {stopped_at}" in p.stdout, p.stdout
    ran = " ".join(calls)
    assert all(m not in ran for m in not_run)
    assert "scan:" in p.stdout                                            # what was written is still scanned


def test_a_failed_verdict_in_the_load_is_a_finding_not_a_stop(run_slot):
    """load_mixed exits 1 when its p95 check fails; the explain share must still be computed."""
    p, calls, _ = run_slot(STUB_LOAD_VERDICT_FAILS="1")
    assert p.returncode == 0 and any("scripts.explain_deadline" in c for c in calls)


def test_a_stale_report_from_an_earlier_run_cannot_pass_for_this_one(run_slot, tmp_path):
    reports = tmp_path / "repo" / "reports"
    reports.mkdir()
    (reports / "felcloud-2026-11-02-smoke.guard.json").write_text('{"server_429": 0, "exceptions": []}')
    p, _, _ = run_slot(STUB_FAIL_STEP="smoke", STUB_FAIL_KIND="crash")
    assert p.returncode == 1 and "SEQUENCE STOPPED at step a" in p.stdout


def test_a_key_in_a_report_fails_the_run(run_slot, tmp_path):
    leaky = STUB.replace('echo "smoke output (ocr_transcribe ok)"', 'echo "smoke output live-key-0123456789abcdef"')
    stub = tmp_path / "stubpy"
    stub.write_text(leaky)
    p, _, _ = run_slot()
    scan_lines = [ln for ln in p.stdout.splitlines() if ln.startswith(("SECRET?", "scan:"))]
    assert p.returncode == 1 and any("the API key" in ln for ln in scan_lines)
    assert not any("live-key-0123456789abcdef" in ln for ln in scan_lines)      # the scan names the place, never the match
    assert "SEQUENCE STOPPED at step scan" in p.stdout


def test_the_gap_between_steps_is_waited_and_can_be_set(run_slot):
    p, _, _ = run_slot(SLOT_GAP_S="0")
    assert "waiting" not in p.stdout
    p, _, _ = run_slot(SLOT_GAP_S="1", STUB_FAIL_STEP="smoke", STUB_FAIL_KIND="429")
    assert "waiting" not in p.stdout                                       # it stopped before the first pause


def test_the_script_refuses_a_missing_env_file_and_a_simulated_profile(run_slot, tmp_path):
    p, calls, _ = run_slot(ENV_FILE=str(tmp_path / "missing"))
    assert p.returncode == 2 and "needs its key profile" in p.stdout and not calls
    # the preflight is a python one-liner: a stub that says "simulated" must stop everything
    stub = tmp_path / "stubpy"
    stub.write_text('#!/usr/bin/env bash\nif [ "$1" = "-" ]; then exit 1; fi\necho "$*" >> "$STUB_LOG"\n')
    p, calls, _ = run_slot()
    assert p.returncode == 2 and "simulates the constraints" in p.stdout and calls == []


# ------------------------------------------------------------------ the python pieces the script relies on
def write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


def test_check_reads_every_report_shape(tmp_path, capsys):
    ok = [write(tmp_path, "a.json", {"server_429": 0, "exceptions": []}),
          write(tmp_path, "b.json", {"provider": {"server_429": 0}, "exceptions": []})]
    assert slot_guard.check(ok) == 0
    for body in ({"server_429": 1, "exceptions": []}, {"provider": {"server_429": 2}, "exceptions": []},
                 {"server_429": 0, "exceptions": [{"task": "x"}]}, {"provider": {}, "exceptions": 3}):
        assert slot_guard.check([write(tmp_path, "c.json", body)]) == 1
    assert slot_guard.check([str(tmp_path / "missing.json")]) == 1
    assert "unreadable" in capsys.readouterr().out
    (tmp_path / "bad.json").write_text("{not json")
    assert slot_guard.check([str(tmp_path / "bad.json")]) == 1


def test_scan_finds_keys_without_printing_them(tmp_path, capsys):
    env = tmp_path / "env"
    env.write_text("LLM_API_KEY=abcd1234efgh5678\n")
    clean = tmp_path / "clean.md"
    clean.write_text("nothing to see: <key-hash> and api_key: <key-hash>\n")
    dirty = tmp_path / "dirty.log"
    dirty.write_text("fine\nthe key abcd1234efgh5678 leaked\n" + "a" * 64 + "\nAuthorization: Bearer abcdefghijklmnop\n"
                     "Rate limit exceeded for api_key: 3f9a1c0be47d55aa\n")
    assert slot_guard.scan([str(clean)], str(env)) == 0
    assert slot_guard.scan([str(dirty)], str(env)) == 1
    out = capsys.readouterr().out
    assert out.count("SECRET?") == 4 and "abcd1234efgh5678" not in out and "3f9a1c0be47d55aa" not in out


def test_guard_counts_what_the_provider_answered():
    g = slot_guard.Guard("t")
    g.spy.log += [{"path": "/v1/chat/completions", "status": 200, "rate_limit_type": None},
                  {"path": "/v1/chat/completions", "status": 429, "rate_limit_type": "requests"},
                  {"path": "/v1/embeddings", "status": 503, "rate_limit_type": None}]
    g.exception("explain", RuntimeError("boom api_key: 3f9a1c0be47d55aa"))
    r = g.report()
    assert r["chat_requests"] == 2 and r["server_429"] == 1 and r["non_200"] == {"429:requests": 1, "503:-": 1}
    assert r["exceptions"][0]["error"] == "RuntimeError: boom api_key: <key-hash>"


def test_explain_share_handles_no_explain_requests():
    rep = {"mode": "m", "cache": "off", "interactive": {"explain": {"n": 0, "degraded_by_deadline": 0}, "by_task": {}}}
    assert share(rep)["share"] is None


# ------------------------------------------------------------------ smoke and eval_check_claim with --guard-out
def _offline_provider(monkeypatch, fail_task=None):
    import json

    from engine import Engine
    from tests.conftest import FnProvider, chat_ok, make_settings

    classify = json.dumps({"intent": "EXPLAIN", "language": "FRENCH", "confusion": "LOW", "complexity": 2, "confidence": 0.9})
    judge = json.dumps({"verdict": "SUPPORTED", "correction": "", "confidence": 0.8})
    fn = lambda body: chat_ok(judge if "verdict" in json.dumps(body["messages"]) else classify
                              if body.get("response_format") else "Une réponse courte.", 50, 10,
                              headers={"x-ratelimit-api_key-remaining-requests": "10"})
    s = make_settings(SIMULATE_CONSTRAINTS="false")
    monkeypatch.setattr("scripts.slot_guard.httpx.AsyncHTTPTransport", lambda: FnProvider(fn))
    for mod in ("scripts.smoke", "scripts.eval_check_claim"):
        monkeypatch.setattr(f"{mod}.load_settings", lambda: s)
    if fail_task:
        real_run = Engine.run

        async def run(self, task, *a, **k):
            if task == fail_task:
                raise RuntimeError("boom Bearer abcdefghijklmnop")
            return await real_run(self, task, *a, **k)
        monkeypatch.setattr(Engine, "run", run)


def test_smoke_writes_its_guard_file(monkeypatch, tmp_path):
    import asyncio

    from scripts import smoke
    _offline_provider(monkeypatch)
    out = tmp_path / "smoke.guard.json"
    monkeypatch.setattr(sys, "argv", ["smoke", "--no-cache", "--guard-out", str(out), "--task", "classify", "--task", "hint"])
    asyncio.run(smoke.main())
    g = json.load(open(out))
    assert g["step"] == "smoke" and g["chat_requests"] >= 2 and g["server_429"] == 0 and g["exceptions"] == []
    assert slot_guard.check([str(out)]) == 0


def test_smoke_counts_an_exception_writes_the_guard_and_exits_1(monkeypatch, tmp_path):
    import asyncio

    from scripts import smoke
    _offline_provider(monkeypatch, fail_task="hint")
    out = tmp_path / "smoke.guard.json"
    monkeypatch.setattr(sys, "argv", ["smoke", "--no-cache", "--guard-out", str(out), "--task", "classify", "--task", "hint"])
    with pytest.raises(SystemExit) as e:
        asyncio.run(smoke.main())
    g = json.load(open(out))
    assert e.value.code == 1 and [x["task"] for x in g["exceptions"]] == ["hint"]
    assert "abcdefghijklmnop" not in json.dumps(g) and slot_guard.check([str(out)]) == 1


def test_smoke_without_a_guard_still_raises(monkeypatch):
    import asyncio

    from scripts import smoke
    _offline_provider(monkeypatch, fail_task="hint")
    monkeypatch.setattr(sys, "argv", ["smoke", "--no-cache", "--task", "hint"])
    with pytest.raises(RuntimeError):
        asyncio.run(smoke.main())


def test_eval_check_claim_writes_its_guard_file(monkeypatch, tmp_path):
    import asyncio

    from scripts import eval_check_claim as E
    _offline_provider(monkeypatch)
    data = tmp_path / "claims.jsonl"
    data.write_text(json.dumps({"id": "c1", "claim": "(x³)' = x²", "expected": "CONTRADICTED", "language": "FRENCH"}) + "\n")
    out, guard = tmp_path / "eval.json", tmp_path / "eval.guard.json"
    monkeypatch.setattr(sys, "argv", ["eval", "--data", str(data), "--out", str(out), "--guard-out", str(guard)])
    asyncio.run(E.main())
    g = json.load(open(guard))
    assert g["step"] == "eval_check_claim" and g["server_429"] == 0 and g["exceptions"] == []
    assert json.load(open(out))["summary"]["overall"]["n"] == 1
