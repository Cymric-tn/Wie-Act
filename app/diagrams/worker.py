"""Diagram maths in a process of its own: one JSON request per line on stdin, one JSON reply per line on stdout.

    python -m diagrams.worker --courses-dir data/courses      (from app/)

Why a process: some expressions that pass the parser's whitelist run for minutes, e.g. ((((9**99)+1)**99+1)**99+1)**99 spends
its time in big-integer arithmetic that holds the GIL. Inside the bridge that would freeze the engine's event loop, and a thread
cannot be stopped. The bridge kills this process at its time limit and starts another. No LLM call is made from here.

Requests:  {"op":"build","text":...,"lang":...,"course_id":...,"mastery":{...},"type":...,"practice":...} -> {"spec":{...}}
           {"op":"check","spec":{...},"answer":{...},"lang":...} -> {"result":{"correct":...,"mistakes":[...]}}
A refusal, in the learner's language: {"refused":"..."}."""
import argparse
import json
import sys
import traceback

MEMORY_LIMIT_BYTES = 2 * 1024 ** 3
LANGS = ("fr", "en", "ar")
TYPES = ("function_graph", "variation_table", "concept_map")


def _limit_memory():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    except (ImportError, ValueError, OSError):
        pass


def handle(req, courses_dir):
    """One request -> one reply. Never raises: anything sympy throws is a refusal the learner can read."""
    from . import Diagram, DiagramError, ParseError, build_from_text, check_table
    from .course import load_repo_course
    from .detect import guess_lang
    from .i18n import t

    text = str(req.get("text") or "")[:300]
    lang = req.get("lang") if req.get("lang") in LANGS else guess_lang(text)
    try:
        if req.get("op") == "check":
            spec = Diagram.model_validate(req.get("spec")).model_dump(by_alias=True, exclude_none=True)
            if spec["type"] != "variation_table":
                return {"refused": t(lang, "err_failed")}
            return {"result": check_table(spec, req.get("answer") or {})}
        course = load_repo_course(courses_dir, req.get("course_id"))
        kind = req.get("type") if req.get("type") in TYPES else None
        return {"spec": build_from_text(text, course, lang, kind, req.get("mastery") or None, req.get("practice"))}
    except ParseError as error:
        print(f"diagram refused: {error}", file=sys.stderr, flush=True)
        return {"refused": t(lang, "err_parse")}
    except DiagramError as error:
        return {"refused": str(error)}
    except Exception:  # noqa: BLE001  sympy raises many kinds of error on unusual input; none of them may end the worker
        traceback.print_exc()
        return {"refused": t(lang, "err_failed")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--courses-dir", required=True)
    args = parser.parse_args()
    out, sys.stdout = sys.stdout, sys.stderr      # stdout carries the protocol, nothing else may print there
    _limit_memory()

    def reply(obj):
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out.flush()

    handle({"op": "build", "text": "x", "type": "function_graph", "lang": "fr"}, args.courses_dir)     # loads sympy before "ready"
    reply({"ready": True})
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            reply({"refused": "bad request"})
            continue
        reply(handle(req if isinstance(req, dict) else {}, args.courses_dir))


if __name__ == "__main__":
    main()
