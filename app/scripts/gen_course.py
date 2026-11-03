"""Writes app/client/src/lib/analyse.generated.js from the engine's course graph (data/courses/analyse.yaml), so the
progress map shows the real concepts offline. Run from the repo root: python app/scripts/gen_course.py [--check]"""
import json
import sys

from engine.course import load_courses

OUT = "app/client/src/lib/analyse.generated.js"


def build() -> str:
    c = load_courses("data/courses")["analyse"]
    concepts = [{"id": k, "unit": v.unit, "requires": list(v.requires), "titles": dict(v.titles), "status": v.status}
                for k, v in c.concepts.items()]
    data = {"id": c.id, "titles": dict(c.titles), "units": [{"id": u.id, "order": u.order, "titles": dict(getattr(u, "titles", {}) or {})} for u in c.units],
            "concepts": concepts}
    return ("// GENERATED from data/courses/analyse.yaml by app/scripts/gen_course.py: do not edit.\n"
            "export const ANALYSE = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n")


if __name__ == "__main__":
    text = build()
    if "--check" in sys.argv:
        sys.exit(0 if open(OUT, encoding="utf-8").read() == text else 1)
    open(OUT, "w", encoding="utf-8").write(text)
    print("wrote", OUT)
