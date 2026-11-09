from pathlib import Path
import yaml
from .spec import DiagramError, ConceptMapPayload, make
from .i18n import t


def load_course(src):
    if isinstance(src, dict):
        return src
    s = str(src)
    text = Path(s).read_text(encoding="utf-8") if "\n" not in s and Path(s).is_file() else s
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or not data.get("units"):
        raise DiagramError("course file missing or empty")
    return data


def concepts_of(course):
    return [c for u in course.get("units", []) for c in u.get("concepts", [])]


def _title(c, lang):
    ti = c.get("title", c["id"])
    return ti if isinstance(ti, str) else ti.get(lang) or ti.get("fr") or ti.get("en") or c["id"]


def _reach(start, adj):
    seen, stack = set(), [start]
    while stack:
        for nxt in adj.get(stack.pop(), []):
            if nxt not in seen:
                seen.add(nxt); stack.append(nxt)
    return seen


def build_concept_map(course_yaml, focus=None, mastery=None, lang="fr"):
    course = load_course(course_yaml)
    cs = concepts_of(course)
    ids = {c["id"] for c in cs}
    edges, seen = [], set()
    for c in cs:
        for p in c.get("requires", []):
            if p in ids:
                edges.append({"from": p, "to": c["id"], "kind": "requires"})
        for r in c.get("related", []):
            key = frozenset((c["id"], r))
            if r in ids and key not in seen:
                seen.add(key); edges.append({"from": c["id"], "to": r, "kind": "related"})
    title = t(lang, "m_title")
    keep = ids
    if focus:
        if focus not in ids:
            raise DiagramError(t(lang, "err_focus", c=focus))
        req = {}, {}
        for e in edges:
            if e["kind"] == "requires":
                req[0].setdefault(e["to"], []).append(e["from"])
                req[1].setdefault(e["from"], []).append(e["to"])
        keep = {focus} | _reach(focus, req[0]) | _reach(focus, req[1])
        title = t(lang, "m_focus", c=_title(next(c for c in cs if c["id"] == focus), lang))
    mastery = mastery or {}
    nodes = [{"id": c["id"], "label": _title(c, lang), **({"mastery": float(mastery[c["id"]])} if c["id"] in mastery else {})}
             for c in cs if c["id"] in keep]
    edges = [e for e in edges if e["from"] in keep and e["to"] in keep]
    return make("concept_map", lang, title, ConceptMapPayload(nodes=nodes, edges=edges, focus=focus or None))
