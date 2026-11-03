"""Second end-to-end round through the app (see scripts/e2e_app.py): the Arabizi explain again, the upload explain with its full
answer and flagged span, and, when --photo is given, an OCR read of a real handwriting photo followed by check_answer on the text
the learner CONFIRMS (--confirmed-text, or the OCR text as read when none is given: the report says which).

    ENV_FILE=.env.real python -m scripts.e2e_round2 --append reports/e2e-felcloud-2026-11-03.md [--photo FILE [--confirmed-text "..."]]

Budget --max-requests (default 15) and --stop-at (default 23:45) as in e2e_app. Appends a section to --append.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import mimetypes
import os
import shutil
import sys

from scripts.e2e_app import NOTES, Q_UPLOAD, Session


class Round2(Session):
    def __init__(self, a):
        super().__init__(a)
        self.photo = a.photo

    def play(self):
        for n, q in enumerate(("derivee mta3 x²", "chnowa el derivee mta3 x² w kifech nal9aha ?"), 1):
            def arabizi(q=q):
                f = self.turn(q)
                r, c = f["result"], f["classification"]["data"]
                answered = bool(r and (r.get("data") or {}).get("body") or (r and r.get("data") and r["data"].get("excerpts")))
                not_found = (r.get("notes") or {}).get("retrieval") == "no_relevant_chunks"
                assert not not_found, "retrieval found nothing"
                return {"detail": {"question": q, "classified": {k: c.get(k) for k in ("intent", "language", "confusion")}, "follow": f["follow"]["task"],
                                   **self.brief(r), "sources": (r.get("notes") or {}).get("sources"), "answer": r.get("text") or ""}}
            self.step(f"R2.{n} Arabizi explain: {q}", 2, arabizi)

        def upload():
            body = {"title": "Asymptotes", "filename": "asymptotes.md", "mime": "text/markdown", "data": base64.b64encode(NOTES.encode()).decode(),
                    "course_id": "analyse", "owner_id": "e2e-prof", "owner_name": "E2E Prof", "role": "professor", "class_id": "E2E"}
            r = self.http.post(f"http://127.0.0.1:{self.node_port}/api/materials", json=body)
            assert r.status_code == 201, r.text
            mid = r.json()["material"]["id"]
            import time
            for _ in range(60):
                stored = next(m for m in self.api("/api/materials?course_id=analyse&learner_id=e2e-prof&role=professor", method="GET")[1]["materials"] if m["id"] == mid)
                if stored["status"] != "processing":
                    break
                time.sleep(0.5)
            assert stored["status"] == "indexed", stored
            res, _ = self.run_task("explain", {"question": Q_UPLOAD}, {"language": "FRENCH"})
            n = res.get("notes") or {}
            spans = {k: n[k] for k in ("ungrounded", "wrong_math", "unchecked_spans", "new_examples", "formulas_verified") if k in n}
            return {"detail": {"sections": len(stored["chunks"]), **self.brief(res), "sources": n.get("sources"), "flag_spans": spans,
                               "all_degrade_reasons": n.get("degrade_reasons"), "answer": res.get("text") or "", "notes_keys": sorted(n)}}
        self.step("R2.3 Upload, then the explain: full answer and flagged span", 1, upload)

        if self.photo:
            def ocr():
                mime = mimetypes.guess_type(self.photo)[0] or "image/jpeg"
                data = "data:%s;base64,%s" % (mime, base64.b64encode(open(self.photo, "rb").read()).decode())
                r, _ = self.run_task("ocr_transcribe", {"image": data}, {"room_id": "e2e-room", "language": "FRENCH"})
                d = r["data"] or {}
                self.ocr_text = d.get("text") or ""
                return {"detail": {**self.brief(r), "text": self.ocr_text, "latex": d.get("latex"), "note": d.get("confidence_note"),
                                   "size_bytes": os.path.getsize(self.photo)}}
            self.step("R2.4 OCR of the real handwriting photo", 1, ocr)

            def check():
                text = self.a.confirmed_text or getattr(self, "ocr_text", "")
                lines = [x.strip() for x in text.splitlines() if x.strip()]
                assert len(lines) >= 2, f"need a problem line and at least one step, got {lines}"
                r, _ = self.run_task("check_answer", {"lines": lines}, {"room_id": "e2e-room", "language": "FRENCH"})
                return {"detail": {**self.brief(r), "text_used": lines, "confirmed_by_the_learner": bool(self.a.confirmed_text), "steps": r["data"]["steps"],
                                   "final_verdict": r["data"]["final_verdict"], "first_wrong": r["data"]["first_wrong"], "feedback": r["data"]["feedback"]}}
            self.step("R2.5 check_answer on the confirmed text", 1, check)

    def append(self, path):
        d = {"steps": self.steps, "stopped": self.stopped, "requests": self.total, "metrics": self.metrics, "engine_calls": len(self.request_ids)}
        L = [f"\n## Round 2, {dt.datetime.now().isoformat(timespec='minutes')} (engine {self.health.get('version')}, "
             f"{self.total} provider requests of {self.a.max_requests}, 429s {self.metrics['server_429']['count']}, stopped: {self.stopped or 'no'})", "",
             "| step | result | requests | seconds |", "|---|---|---|---|"]
        for s in self.steps:
            L.append(f"| {s['step']} | {s['status']}{(': ' + s['why']) if s.get('why') else ''} | {s.get('requests', '-')} | {s.get('seconds', '-')} |")
        for s in self.steps:
            if s.get("detail"):
                det = dict(s["detail"])
                ans = det.pop("answer", None)
                L += ["", f"### {s['step']}", "", "```json", json.dumps(det, ensure_ascii=False, indent=1), "```"]
                if ans is not None:
                    L += ["", "Full answer:", "", "```text", ans, "```"]
        L += ["", f"Engine numbers for these calls ({len(self.request_ids)} engine calls, by request id):", "", "```json", json.dumps(self.metrics, ensure_ascii=False, indent=1), "```"]
        text = "\n".join(L) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.splitext(path)[0] + "-round2.json", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        print("appended to", path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--append", default="reports/e2e-felcloud-2026-11-03.md")
    ap.add_argument("--max-requests", type=int, default=15)
    ap.add_argument("--stop-at", default="23:45")
    ap.add_argument("--photo")
    ap.add_argument("--confirmed-text")
    a = ap.parse_args()
    s = Round2(a)
    try:
        s.run()
    finally:
        s.stop()
    s.append(a.append)
    shutil.rmtree(s.tmp, ignore_errors=True)
    return 0 if not s.stopped else 1


if __name__ == "__main__":
    sys.exit(main())
