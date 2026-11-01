"""docs/ENGINE_API.md is generated from real engine output: it must not drift from the code."""
import glob
import json
import re

from engine.result import Result
from scripts import gen_api_examples as g


def examples():
    return {p: json.load(open(p, encoding="utf-8")) for p in sorted(glob.glob("docs/examples/*.json"))}


def test_every_example_is_a_valid_result():
    ex = examples()
    assert len(ex) >= 45
    for p, d in ex.items():
        assert d.pop("_source") in ("real", "canned"), p
        Result.model_validate(d)


def test_doc_and_examples_are_up_to_date():
    # fails when engine behaviour changed: run `python -m scripts.gen_api_examples --doc`
    assert g.main.__module__ and _check() == 0


def _check() -> int:
    import asyncio
    ex = asyncio.run(g.build())
    stale = [n for n, (s, r) in ex.items() if open(f"docs/examples/{n}.json", encoding="utf-8").read() != g.render(s, r)]
    assert not stale, f"stale examples: {stale}"
    assert open(g.DOC_OUT, encoding="utf-8").read() == g.render_doc(ex), "docs/ENGINE_API.md is stale"
    return 0


def test_every_event_name_in_the_examples_is_documented():
    doc = open("docs/ENGINE_API.src.md", encoding="utf-8").read()
    section = doc.split("## 6. Events")[1].split("## 7.")[0]
    documented = set(re.findall(r"`([a-z_]+)`", section))
    used = {e["name"] for d in examples().values() for e in d["events"]}
    assert used <= documented, f"undocumented events: {sorted(used - documented)}"


def test_every_degrade_reason_in_the_examples_is_documented():
    doc = open("docs/ENGINE_API.src.md", encoding="utf-8").read()
    section = doc.split("## 5. Degrade reasons")[1].split("## 6.")[0]
    documented = set(re.findall(r"`([a-z_:*]+)`", section))
    used = {d["degrade_reason"] for d in examples().values() if d["degrade_reason"]}
    assert used <= documented, f"undocumented reasons: {sorted(used - documented)}"
