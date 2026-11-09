import re
import unicodedata
from functools import lru_cache
from pathlib import Path
import yaml
from .concept_map import concepts_of, load_course

GENERIC = {"derivee", "derivative", "notions", "concept", "function", "fonction"}
TRIG_DIR = Path(__file__).parent / "triggers"
ORDER = ("variation_table", "concept_map", "function_graph")
MATH = re.compile(r"(?:f\s*\(\s*x\s*\)\s*=|sqrt|ln|exp|sin|cos|tan|pi|[0-9x+\-−*/^().,²³√×·⁻ˣπ]|\be\b|\s)+")


@lru_cache(maxsize=1)
def triggers():
    merged = {}
    for p in sorted(TRIG_DIR.glob("*.yaml")):
        for k, words in (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).items():
            merged.setdefault(k, []).extend(str(w).lower() for w in words)
    return {k: sorted(set(v), key=len, reverse=True) for k, v in merged.items()}


def guess_lang(text):
    if re.search(r"[\u0600-\u06FF]", text):
        return "ar"
    low = text.lower()
    if re.search(r"[éèêàçùâîôû]|\b(le|la|les|de|du|des|et|sa|courbe|graphe|trace\w*|tangente|tableau|carte|notions)\b", low):
        return "fr"
    if re.search(r"\b(the|and|of|plot|graph|curve|tangent|derivative|map|concept|table|draw)\b", low):
        return "en"
    return "fr"


def _fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", str(s).lower()) if not unicodedata.combining(c))


def _find_concept(low, course):
    low = _fold(low)
    best = None
    for c in concepts_of(course):
        names = [c["id"]] + ([c["title"]] if isinstance(c.get("title"), str) else list(c.get("title", {}).values()))
        for n in names:
            n = _fold(n)
            if n and n in low and (best is None or len(n) > best[0]):
                best = (len(n), c["id"])
    if best:
        return best[1]
    words = set(re.findall(r"\w{4,}", low))  # fallback: one concept whose title words all appear
    hits = [c["id"] for c in concepts_of(course)
            if any(w in words for n in ([c["title"]] if isinstance(c.get("title"), str) else c.get("title", {}).values())
                   for w in re.findall(r"\w{5,}", _fold(n)) if w not in GENERIC)]
    return hits[0] if len(set(hits)) == 1 else None


def detect_request(text, course_yaml=None):
    tr, low = triggers(), text.lower()
    res = {"type": None, "expression": None, "tangent_at": None, "with_derivative": False,
           "focus": None, "practice": any(w in low for w in tr.get("practice", [])), "lang": guess_lang(text)}
    for typ in ORDER:
        if any(w in low for w in tr.get(typ, [])):
            res["type"] = typ
            break
    work = low
    tw = "|".join(re.escape(w) for w in tr.get("tangent", []))
    m = re.search(rf"(?:{tw})\s*(?:x\s*=\s*)?(-?\d+(?:[.,]\d+)?)", work) if tw else None
    if m:
        res["tangent_at"] = m.group(1).replace(",", ".")
        work = work.replace(m.group(0), " ")
    for w in tr.get("derivative", []):
        if w in work:
            res["with_derivative"] = True
            work = work.replace(w, " ")
    for k in (*ORDER, "practice"):
        for w in tr.get(k, []):
            work = work.replace(w, " ")
    cands = [c.group().strip(" .,") for c in MATH.finditer(work) if "x" in c.group()]
    if cands:
        res["expression"] = max(cands, key=len)
    if res["type"] == "concept_map" and course_yaml is not None:
        res["focus"] = _find_concept(low, load_course(course_yaml))
    return res
