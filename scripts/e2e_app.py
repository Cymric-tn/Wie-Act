"""End-to-end session through the web app's HTTP API: Node server -> bridge -> engine -> provider.

    ENV_FILE=.env.real python -m scripts.e2e_app --out reports/e2e-felcloud-2026-11-03     # FelCloud: spends the team's quota
    python -m scripts.e2e_app --fake --out /tmp/rehearsal                                  # the engine's FakeEngine: no quota

Starts the bridge (the real engine, or --fake) and the Node server on free ports with a shared secret, then plays ONE
learner's session and a professor's upload, step by step. A step is skipped, and listed as not run, when the budget
(--max-requests provider requests, default 40) or the cut-off time (--stop-at HH:MM, default 23:45) is reached; a server
429 or an exception stops the session. Writes <out>.md and <out>.json; scans them for the key before leaving.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import httpx

NODE = os.environ.get("NODE", os.path.expanduser("~/.nvm/versions/node/v24.16.0/bin/node"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTES = ("# Asymptotes obliques\n\nUne asymptote oblique est une droite d'équation y = ax + b dont la courbe se rapproche "
         "quand x tend vers l'infini. On la cherche quand f(x)/x tend vers un nombre a non nul.\n")
Q_UPLOAD = "C'est quoi une asymptote oblique ?"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def png_of_text(lines: list[str]) -> str:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1400, 400), (250, 250, 247))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((50, 60 + i * 110), line, fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class Session:
    def __init__(self, a):
        self.a = a
        self.token = "e2e-" + base64.urlsafe_b64encode(os.urandom(12)).decode()
        self.bridge_port, self.node_port = free_port(), free_port()
        self.tmp = tempfile.mkdtemp(prefix="ednova-e2e-")
        self.procs: list[subprocess.Popen] = []
        self.steps: list[dict] = []
        self.stopped: str | None = None
        self.learner = "e2e-learner-1"
        self.ctx = {"course_id": "analyse", "class_id": "E2E", "language": None}

    # ---------------------------------------------------------------- processes
    def start(self):
        env = {**os.environ, "ENGINE_SERVICE_TOKEN": self.token}
        cmd = [sys.executable, os.path.join(ROOT, "app/bridge/bridge.py"), "--port", str(self.bridge_port)]
        if self.a.fake:
            cmd += ["--fake", "--fake-speed", "0"]
        self.procs.append(subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=open(os.path.join(self.tmp, "bridge.log"), "w"), stderr=subprocess.STDOUT))
        self.procs.append(subprocess.Popen(
            [NODE, "--disable-warning=ExperimentalWarning", "index.js"], cwd=os.path.join(ROOT, "app/server"),
            env={**env, "PORT": str(self.node_port), "ENGINE_URL": f"http://127.0.0.1:{self.bridge_port}",
                 "DB_PATH": os.path.join(self.tmp, "e2e.db"), "CLIENT_ORIGINS": "http://localhost:5173"},
            stdout=open(os.path.join(self.tmp, "node.log"), "w"), stderr=subprocess.STDOUT))
        self.http = httpx.Client(timeout=180)
        self.bridge = httpx.Client(base_url=f"http://127.0.0.1:{self.bridge_port}", headers={"X-Engine-Token": self.token}, timeout=30)
        end = time.time() + 60
        while time.time() < end:
            try:
                h = self.bridge.get("/health").json()
                s = self.http.get(f"http://127.0.0.1:{self.node_port}/health").json()
                if s.get("ai", {}).get("connected"):
                    self.health = h
                    return
            except httpx.HTTPError:
                pass
            for p in self.procs:
                if p.poll() is not None:
                    raise SystemExit(f"a process exited with {p.returncode}: see {self.tmp}/*.log")
            time.sleep(0.5)
        raise SystemExit("the app did not come up")

    def stop(self):
        for p in reversed(self.procs):
            p.terminate()
        for p in self.procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()

    # ---------------------------------------------------------------- budget and clock
    def used(self) -> int:
        s = self.bridge.get("/metrics/summary").json()
        return s["provider_requests"]["total"]

    def server_429(self) -> int:
        return self.bridge.get("/metrics/summary").json()["server_429"]["count"]

    def may_send(self, need: int) -> str | None:
        if self.stopped:
            return self.stopped
        now = dt.datetime.now().strftime("%H:%M")
        if not self.a.fake and now >= self.a.stop_at:
            return f"cut-off {self.a.stop_at} reached"
        if self.base_used is not None and self.used() - self.base_used + need > self.a.max_requests:
            return f"budget of {self.a.max_requests} requests reached"
        return None

    # ---------------------------------------------------------------- calls
    def api(self, path, body=None, method="POST"):
        url = f"http://127.0.0.1:{self.node_port}{path}"
        if method == "GET":
            r = self.http.get(url)
            return r.status_code, r.json()
        with self.http.stream("POST", url, json=body) as r:
            if r.status_code != 200:
                r.read()
                return r.status_code, r.json()
            lines = [json.loads(x) for x in r.iter_lines() if x]
        return 200, lines

    def step(self, name, need, fn):
        entry = {"step": name, "requests_expected": need}
        why = self.may_send(need)
        if why:
            entry.update(status="not run", why=why)
            self.steps.append(entry)
            return
        before, t0 = self.used(), time.time()
        try:
            entry.update(fn() or {})
            entry["status"] = entry.get("status", "ok")
        except AssertionError as e:
            entry.update(status="FAILED CHECK", why=str(e))
        except Exception as e:   # noqa: BLE001
            entry.update(status="ERROR", why=f"{type(e).__name__}: {str(e)[:200]}")
            self.stopped = "an exception"
        entry["requests"] = self.used() - before
        entry["seconds"] = round(time.time() - t0, 1)
        if self.server_429():
            self.stopped = "a server 429"
        self.steps.append(entry)
        print(f"{entry['status']:12} {name}  ({entry['requests']} requests, {entry['seconds']} s)", flush=True)

    @staticmethod
    def final(lines):
        return next(x for x in lines if x["type"] == "result")

    def brief(self, r):
        return {"task": r.get("task"), "model_class": r.get("model_class_used"), "from_cache": r.get("from_cache"),
                "degraded": r.get("degraded"), "degrade_reason": r.get("degrade_reason"), "latency_ms": r.get("latency_ms"),
                "events": [e["name"] for e in r.get("events", [])][:14]}

    # ---------------------------------------------------------------- the session
    def run(self):
        self.start()
        try:
            self.base_used = self.used()
            self.play()
        finally:
            self.total = self.used() - self.base_used
            self.metrics = self.bridge.get("/metrics/summary").json()
            self.stop()

    def turn(self, message, **extra):
        status, lines = self.api("/api/ai/turn", {"message": message, "learner_id": self.learner,
                                                  "context": {"course_id": "analyse", "class_id": "E2E", **extra.pop("context", {})}, **extra})
        assert status == 200, f"HTTP {status}: {lines}"
        return self.final(lines)

    def run_task(self, task, inputs, context=None, priority="INTERACTIVE"):
        status, lines = self.api("/api/ai/run", {"task": task, "inputs": inputs, "learner_id": self.learner, "priority": priority,
                                                 "context": {"course_id": "analyse", "class_id": "E2E", **(context or {})}})
        assert status == 200, f"HTTP {status}: {lines}"
        return self.final(lines)["result"], [x for x in lines if x["type"] == "event"]

    def play(self):
        out = {}

        def french_explain():
            f = self.turn("C'est quoi la dérivée d'un produit ?")
            r = f["result"]
            c = f["classification"]["data"]
            assert f["follow"]["task"] == "explain", f["follow"]
            assert r["text"] or r["data"], "no answer"
            out["fr"] = {"classified": {k: c.get(k) for k in ("intent", "language", "confusion")}, **self.brief(r),
                         "sources": (r.get("notes") or {}).get("sources"), "answer": (r["text"] or "")[:400]}
            return {"detail": out["fr"]}
        self.step("1. French explain (classify, then explain)", 2, french_explain)

        def arabizi_explain():
            f = self.turn("chnowa el derivee mta3 x² w kifech nal9aha ?")
            r = f["result"]
            c = f["classification"]["data"]
            assert r and (r["text"] or r["data"]), "no answer"
            return {"detail": {"classified": {k: c.get(k) for k in ("intent", "language", "confusion")}, "follow": f["follow"]["task"],
                               **self.brief(r), "answer": (r["text"] or "")[:400]}}
        self.step("2. Arabizi explain", 2, arabizi_explain)

        def exercise():
            quiz, _ = self.run_task("quiz", {"concept": "puissance", "intro": False})
            ex = quiz["data"]["exercises"][0]
            assert "solution" not in ex and "hints" not in ex, "the solution reached the browser"
            before = self.api(f"/api/ai/mastery?learner_id={self.learner}&course_id=analyse", method="GET")[1]["mastery"].get(ex["concept"], {})
            wrong = self.final(self.api(f"/api/ai/exercises/{ex['id']}/answer", {"attempt": "x^3", "learner_id": self.learner, "context": {"course_id": "analyse", "language": "FRENCH"}})[1])
            right = self.final(self.api(f"/api/ai/exercises/{ex['id']}/answer", {"attempt": "4x^3", "learner_id": self.learner, "context": {"course_id": "analyse", "language": "FRENCH"}})[1])
            assert wrong["result"]["data"]["verdict"] == "INCORRECT", wrong["result"]["data"]
            assert right["result"]["data"]["verdict"] == "CORRECT", right["result"]["data"]
            m = right["mastery"]
            assert m["attempts"] >= 2 and m["value"] > (wrong["mastery"] or {}).get("value", 0), (wrong["mastery"], m)
            return {"detail": {"exercise": ex["id"], "statement": ex["statement"], "wrong": {"verdict": "INCORRECT", "feedback": wrong["result"]["data"].get("feedback"), "mastery": wrong["mastery"]},
                               "right": {"verdict": "CORRECT", "mastery": m}, "mastery_before": before, **{"graded_by": right["result"]["data"].get("graded_by")}}}
        self.step("3. Exercise answered wrong, then right (mastery changes)", 1, exercise)

        def board_check():
            r, _ = self.run_task("check_answer", {"lines": ["f(x) = 3x² + 5x", "f'(x) = 6x + 5", "f'(0) = 6"]}, {"room_id": "e2e-room", "language": "FRENCH"})
            d = r["data"]
            assert d["final_verdict"] == "INCORRECT" and d["first_wrong"] == 2, d
            return {"detail": {**self.brief(r), "steps": d["steps"], "first_wrong": d["first_wrong"], "feedback": d["feedback"], "graded_by": d.get("graded_by")}}
        self.step("4. Board check with one wrong line", 1, board_check)

        def compound():
            r, _ = self.run_task("check_claim", {"claim": "(x²)' = 2x et la dérivée d'une constante est 1"}, {"room_id": "e2e-room", "language": "FRENCH"})
            d = r["data"]
            assert d["verdict"] == "PARTLY" and len(d["parts"]) == 2, d
            return {"detail": {**self.brief(r), "verdict": d["verdict"], "correction": d["correction"], "parts": d["parts"]}}
        self.step("5. Compound fact-check", 1, compound)

        def ocr():
            r, _ = self.run_task("ocr_transcribe", {"image": png_of_text(["Dériver f(x) = 3x² + 5x", "f'(x) = 6x + 5"])}, {"room_id": "e2e-room", "language": "FRENCH"})
            d = r["data"] or {}
            assert d.get("text"), r
            return {"detail": {**self.brief(r), "text": d.get("text"), "latex": d.get("latex"), "note": d.get("confidence_note")}}
        self.step("6. OCR read", 1, ocr)

        def prep_page():
            plan, _ = self.run_task("prep_plan", {"course_id": "analyse", "days_left": 5, "minutes_per_day": 30})
            first = plan["data"]["sessions"][0]["items"][0]["concept"]
            page, _ = self.run_task("generate_page", {"concept": first}, {"language": "FRENCH"}, "BACKGROUND")
            d = page["data"]
            assert d["worked_example"] and d["quiz"], d.keys()
            assert "solution" not in json.dumps(d["quiz"]), "a quiz solution reached the browser"
            return {"detail": {"plan_days": len(plan["data"]["sessions"]), "first_concept": first, "title": d["title"], "degraded": page["degraded"], "degrade_reason": page["degrade_reason"],
                               "from_cache": page["from_cache"], "quiz": len(d["quiz"]), "explanation": (d["explanation"] or "")[:300]}}
        self.step("7. One prep page", 1, prep_page)

        def upload():
            body = {"title": "Asymptotes", "filename": "asymptotes.md", "mime": "text/markdown", "data": base64.b64encode(NOTES.encode()).decode(),
                    "course_id": "analyse", "owner_id": "e2e-prof", "owner_name": "E2E Prof", "role": "professor", "class_id": "E2E"}
            r = self.http.post(f"http://127.0.0.1:{self.node_port}/api/materials", json=body)
            assert r.status_code == 201, r.text
            mid = r.json()["material"]["id"]
            stored = None
            for _ in range(60):
                lst = self.api(f"/api/materials?course_id=analyse&learner_id=e2e-prof&role=professor", method="GET")[1]["materials"]
                stored = next(m for m in lst if m["id"] == mid)
                if stored["status"] != "processing":
                    break
                time.sleep(0.5)
            assert stored["status"] == "indexed", stored
            before = self.api("/api/ai/run", {"task": "explain", "inputs": {"question": "Comment fonctionne la photosynthèse ?"}, "learner_id": self.learner,
                                              "context": {"course_id": "analyse", "class_id": "E2E", "language": "FRENCH"}})  # not in the course: free
            res, _ = self.run_task("explain", {"question": Q_UPLOAD}, {"language": "FRENCH"})
            sources = (res.get("notes") or {}).get("sources") or []
            assert any("professor/e2e-prof/" in s for s in sources), f"the upload was not used: {sources} / {res.get('notes')}"
            return {"detail": {"sections": len(stored["chunks"]), "chunk_ids": [c["id"] for c in stored["chunks"]][:3], **self.brief(res), "sources": sources,
                               "answer": (res["text"] or "")[:400]}}
        self.step("8. Upload, then an explain that uses it", 1, upload)

    # ---------------------------------------------------------------- report
    def report(self, out):
        key = os.environ.get("LLM_API_KEY", "")
        d = {"date": dt.datetime.now().isoformat(timespec="seconds"), "mode": "fake" if self.a.fake else "FelCloud (real engine)",
             "engine_version": self.health.get("version"), "provider_requests": self.total, "max_requests": self.a.max_requests,
             "stopped_because": self.stopped, "steps": self.steps, "metrics": self.metrics}
        with open(out + ".json", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        lines = [f"# End-to-end session through the app, {d['date'][:10]}", "",
                 f"Mode: **{d['mode']}**, engine {d['engine_version']}. Path: browser API → Node server → bridge → engine → provider. "
                 f"**{d['provider_requests']} provider requests** (budget {self.a.max_requests}); server 429s {self.metrics['server_429']['count']}; "
                 f"stopped early: {self.stopped or 'no'}.", "", "| step | result | requests | seconds |", "|---|---|---|---|"]
        for s in self.steps:
            lines.append(f"| {s['step']} | {s['status']}{(': ' + s['why']) if s.get('why') else ''} | {s.get('requests', '-')} | {s.get('seconds', '-')} |")
        for s in self.steps:
            if s.get("detail"):
                lines += ["", f"### {s['step']}", "", "```json", json.dumps(s["detail"], ensure_ascii=False, indent=1), "```"]
        not_run = [s for s in self.steps if s["status"] == "not run"]
        if not_run:
            lines += ["", "## Not run (moves to the next slot)", ""] + [f"- {s['step']}: {s['why']}" for s in not_run]
        lines += ["", "## Engine numbers for the session", "", "```json", json.dumps(self.metrics, ensure_ascii=False, indent=1), "```"]
        text = "\n".join(lines) + "\n"
        if key and key in text:
            raise SystemExit("the report contains the API key: not written")
        with open(out + ".md", "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote", out + ".md")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--out", default="reports/e2e-felcloud-2026-11-03")
    ap.add_argument("--max-requests", type=int, default=40)
    ap.add_argument("--stop-at", default="23:45")
    a = ap.parse_args()
    s = Session(a)
    try:
        s.run()
    finally:
        s.stop()
    s.report(a.out)
    shutil.rmtree(s.tmp, ignore_errors=True)
    return 0 if not s.stopped else 1


if __name__ == "__main__":
    sys.exit(main())
