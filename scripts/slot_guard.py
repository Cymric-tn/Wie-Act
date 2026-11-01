"""Guards for a weekday FelCloud slot (scripts/slot_run.sh).

    python -m scripts.slot_guard check reports/x.guard.json reports/y.json     # exit 1 on a server 429 or an exception
    python -m scripts.slot_guard scan --env-file .env.real reports/felcloud-2026-11-02-*   # exit 1 on a key-like string

`check` reads any of the report shapes the slot steps write: a guard file ({server_429, exceptions}), or a mixed-load /
probe report ({provider: {server_429}, exceptions: [...]}). A missing or unreadable file counts as a failure: a step that
died before writing its report must stop the sequence too. `scan` never prints a match, only file, line and kind.
The Guard class is what smoke.py and eval_check_claim.py use to count what the provider answered.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter

import httpx


class Guard:
    """A spy on the transport (every status the provider returned) and a list of exceptions out of engine.run."""

    def __init__(self, step: str):
        from scripts.load_mixed import SpyTransport
        self.step, self.exceptions = step, []
        self.spy = SpyTransport(httpx.AsyncHTTPTransport(), time.monotonic)

    @property
    def transport(self) -> httpx.AsyncBaseTransport:
        return self.spy

    def exception(self, task: str, error: BaseException) -> None:
        from gatekeeper.errors import scrub
        self.exceptions.append({"task": task, "error": f"{type(error).__name__}: {scrub(str(error))[:200]}"})

    def report(self) -> dict:
        log = self.spy.log
        chat = [x for x in log if x["path"].endswith("/chat/completions")]
        return {"step": self.step, "chat_requests": len(chat),
                "non_200": dict(Counter(f"{x['status']}:{x['rate_limit_type'] or '-'}" for x in log if x["status"] != 200)),
                "server_429": sum(1 for x in log if x["status"] == 429), "exceptions": list(self.exceptions)}

    def write(self, path: str) -> dict:
        rep = self.report()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        return rep


def counts(report: dict) -> tuple[int, int]:
    """(server 429s, exceptions) of any report shape."""
    n429 = report.get("server_429", (report.get("provider") or {}).get("server_429", 0))
    exc = report.get("exceptions", [])
    return int(n429 or 0), (exc if isinstance(exc, int) else len(exc))


def check(paths: list[str]) -> int:
    bad = 0
    for p in paths:
        try:
            n429, exc = counts(json.load(open(p, encoding="utf-8")))
        except (OSError, ValueError) as e:
            print(f"STOP {p}: unreadable ({type(e).__name__}); the step did not finish")
            bad += 1
            continue
        if n429 or exc:
            print(f"STOP {p}: {n429} server 429(s), {exc} exception(s)")
            bad += 1
        else:
            print(f"ok   {p}: 0 server 429s, 0 exceptions")
    return 1 if bad else 0


PATTERNS = {"64-hex": re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])"),
            "bearer": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{12,}"), "sk-key": re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"),
            "echoed key hash": re.compile(r"api[_ -]?key(?: hash)?\s*[:=]\s*(?!<key-hash>)[A-Za-z0-9_\-]{8,}", re.I)}


def scan(paths: list[str], env_file: str | None) -> int:
    from dotenv import dotenv_values
    key = (dotenv_values(env_file).get("LLM_API_KEY") or "").strip() if env_file else ""
    hits = 0
    for p in paths:
        try:
            lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            kinds = [k for k, rx in PATTERNS.items() if rx.search(line)]
            if len(key) >= 8 and key in line:
                kinds.append("the API key")
            for k in kinds:
                print(f"SECRET? {p}:{n}: {k}")
                hits += 1
    print(f"scan: {hits} hit(s) in {len(paths)} file(s)")
    return 1 if hits else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("files", nargs="+")
    sc = sub.add_parser("scan")
    sc.add_argument("--env-file")
    sc.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    return check(a.files) if a.cmd == "check" else scan(a.files, a.env_file)


if __name__ == "__main__":
    sys.exit(main())
